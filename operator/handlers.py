"""A kopf handler for the Jupyter CRD."""

import json
import logging
import random
import os
import string
from typing import Any, Dict, Optional

import kubernetes
import kopf

# Configuration of underlying API requests.
#
# Request timeout (from Python Kubernetes API)
#   If one number provided, it will be total request
#   timeout. It can also be a pair (tuple) of
#   (connection, read) timeouts.
_REQUEST_TIMEOUT = (30, 20)

# Some (key) default deployment variables...
_DEFAULT_IMAGE: str = "jupyter/minimal-notebook:notebook-6.3.0"
_DEFAULT_SA: str = "default"
_DEFAULT_CPU_LIMIT: str = "1"
_DEFAULT_CPU_REQUEST: str = "10m"
_DEFAULT_MEM_LIMIT: str = "1Gi"
_DEFAULT_MEM_REQUEST: str = "256Mi"
_DEFAULT_USER_ID: int = 1000
_DEFAULT_GROUP_ID: int = 100
_DEFAULT_INGRESS_PROXY_BODY_SIZE: str = "500m"
# The default ingress domain (must be provided).
# The user can provide an alternative via the CR.
_DEFAULT_INGRESS_DOMAIN: str = os.environ["INGRESS_DOMAIN"]
# The ingress TLS secret.
# If provided it is used as the Ingress secret
# and cert-manager is avoided.
# The uer can provide their own via the CR.
_DEFAULT_INGRESS_TLS_SECRET: Optional[str] = os.environ.get("INGRESS_TLS_SECRET")
# The ingress class
_DEFAULT_INGRESS_CLASS: str = "nginx"

# Apply Pod Priority class?
# Any value results in setting the Pod's Priority Class
_APPLY_POD_PRIORITY_CLASS: Optional[str] = os.environ.get("JO_APPLY_POD_PRIORITY_CLASS")
# If set and JO_APPLY_POD_PRIORITY_CLASS is set
# this value will be used if now alternative is available.
_DEFAULT_POD_PRIORITY_CLASS: str = os.environ.get(
    "JO_DEFAULT_POD_PRIORITY_CLASS", "im-application-low"
)


# The cert-manager issuer,
# expected if a INGRESS_TLS_SECRET is not defined.
ingress_cert_issuer: Optional[str] = os.environ.get("INGRESS_CERT_ISSUER")

# Application node selection
_POD_NODE_SELECTOR_KEY: str = os.environ.get(
    "JO_POD_NODE_SELECTOR_KEY", "informaticsmatters.com/purpose-application"
)
_POD_NODE_SELECTOR_VALUE: str = os.environ.get("JO_POD_NODE_SELECTOR_VALUE", "yes")

# A custom startup script.
# This is executed as the container "command"
# It writes a new a .bashrc and copies
# the .bash_profile and jupyter_notebook_config.json file into place
# before running 'jupyter lab'.
#
# Working directory is the Project directory,
# and HOME is the project instance directory
# (where the bash and bash_profile files are written)
#
# As part of the startup we erase the existing '~/.bashrc' and,
# as a minimum, set a more suitable PS1 (see ch2385).
# 'conda init' then puts its stuff into the same file.
_NOTEBOOK_STARTUP: str = r"""#!/bin/bash
echo "PS1='\$(pwd) \$UID$ '" > ~/.bashrc
echo "umask 0002" >> ~/.bashrc
conda init
source ~/.bashrc

if [ ! -f ~/.bash_profile ]; then
    echo "Copying bash_profile into place"
    cp /etc/.bash_profile ~
fi

if [ ! -f ~/jupyter_notebook_config.json ]; then
    echo "Copying config into place"
    cp /etc/jupyter_notebook_config.json ~
fi

jupyter lab --config=~/jupyter_notebook_config.json
"""

# The bash-profile
# which simply launches the .bashrc
_BASH_PROFILE: str = """if [ -f ~/.bashrc ]; then
    source ~/.bashrc
fi
"""

# The Jupyter jupyter_notebook_config.json file.
# A ConfigMap whose content is written into '/etc'
# and copied to the $HOME/.jupyter by the notebook_startup
# script (above).
_NOTEBOOK_CONFIG: str = """{
  "ServerApp": {
    "token": "%(token)s",
    "base_url": "%(base_url)s",
    "ip": "0.0.0.0"
  }
}
"""


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_: Any) -> None:
    """The operator startup handler."""
    # Here we adjust the logging level
    settings.posting.level = logging.INFO

    # Attempt to protect ourselves from missing watch events.
    # See https://github.com/nolar/kopf/issues/698
    # Added in an attempt to prevent the operator "falling silent"
    settings.watching.server_timeout = 120
    settings.watching.client_timeout = 150


@kopf.on.create("squonk.it", "v1alpha3", "jupyternotebooks", id="jupyter")
def create_v1alpha3(
    spec: Dict[str, Any], name: str, namespace: str, **_: Any
) -> Dict[str, Any]:
    """Handler for legacy CRD create events."""
    raise kopf.PermanentError("No longer supported")


# For TEMPORARY errors (i.e. those that are not kopf.PermanentError)
# we retry after 20 seconds and only retry 6 times
@kopf.on.create(
    "squonk.it", "v2", "jupyternotebooks", id="jupyter", backoff=20, retries=6
)
def create(spec: Dict[str, Any], name: str, namespace: str, **_: Any) -> Dict[str, Any]:
    """Handler for CRD create events.
    Here we construct the required Kubernetes objects,
    adopting them in kopf before using the corresponding Kubernetes API
    to create them.

    We handle errors typically raising 'kopf.PermanentError' to prevent
    Kubernetes constantly calling back for a given create.
    """

    logging.info("Creating %s (namespace=%s)...", name, namespace)
    logging.info("Incoming %s spec=%s", name, spec)

    # All Data-Manager provided material
    # will be namespaced under the 'imDataManager' property
    material: Dict[str, Any] = spec.get("imDataManager", {})

    notebook_interface = material.get("notebook", {}).get("interface", "lab")

    image = material.get("image", _DEFAULT_IMAGE)
    image_parts = image.split(":")
    image_tag = "latest" if len(image_parts) == 1 else image_parts[1]
    image_pull_policy = (
        "Always" if image_tag.lower() in ["latest", "stable"] else "IfNotPresent"
    )

    service_account = material.get("serviceAccountName", _DEFAULT_SA)

    resources = material.get("resources", {})
    cpu_limit = resources.get("limits", {}).get("cpu", _DEFAULT_CPU_LIMIT)
    cpu_request = resources.get("requests", {}).get("cpu", _DEFAULT_CPU_REQUEST)
    memory_limit = resources.get("limits", {}).get("memory", _DEFAULT_MEM_LIMIT)
    memory_request = resources.get("requests", {}).get("memory", _DEFAULT_MEM_REQUEST)

    # Data Manager API compliance.
    #
    # The user and group IDs we're asked to run as.
    # The files in the container project volume will be owned
    # by this user and group. We must run as group 100.
    # We use the supplied group ID and pass that into the container
    # as the Kubernetes 'File System Group' (fsGroup).
    # This should allow us to run and manipulate the files.
    sc_run_as_user = material.get("securityContext", {}).get(
        "runAsUser", _DEFAULT_USER_ID
    )
    sc_run_as_group = material.get("securityContext", {}).get(
        "runAsGroup", _DEFAULT_GROUP_ID
    )

    # Project storage
    project_claim_name = material.get("project", {}).get("claimName")
    project_id = material.get("project", {}).get("id")

    ingress_proxy_body_size = material.get(
        "ingressProxyBodySize", _DEFAULT_INGRESS_PROXY_BODY_SIZE
    )

    ingress_class = material.get("ingressClass", _DEFAULT_INGRESS_CLASS)
    ingress_domain = material.get("ingressDomain", _DEFAULT_INGRESS_DOMAIN)
    ingress_tls_secret = material.get("ingressTlsSecret", _DEFAULT_INGRESS_TLS_SECRET)
    ingress_path = f"/{name}"

    # ConfigMaps
    # ----------

    core_api = kubernetes.client.CoreV1Api()

    # We might be here as another attempt to create the same application
    # (an exception may have caused a prior execution to fail).
    # The operator is configured to re-try on such occasions
    # (with a period based on out 'backoff' value set in our decorator).
    # Here, we need to check for the existence of the 'config' ConfigMap.
    # If it exists, we read it and get the token we had previously set.
    # If there is no ConfigMap (404) we are free to set a new token.
    cm_name = f"config-{name}"
    json_data_key = "jupyter_notebook_config.json"
    token = ""
    config_cm = None
    try:
        config_cm = core_api.read_namespaced_config_map(cm_name, namespace)
    except kubernetes.client.exceptions.ApiException as ex:
        # We 'expect' 404, anything else is an error
        if ex.status != 404:
            logging.error(
                "Got ApiException [%s/%s] getting existing CONFIG ConfigMap %s",
                ex.status,
                ex.reason,
                cm_name,
            )
            raise ex
    if config_cm:
        # We retrieved an existing CONFIG - extract the token from it
        json_data = json.loads(config_cm.data[json_data_key])
        token = json_data["ServerApp"]["token"]
        logging.debug(
            "Retrieved prior token from CONFIG ConfigMap %s (%s)",
            cm_name,
            token,
        )
    else:
        # No prior config - we're free to allocate a new token
        characters = string.ascii_letters + string.digits
        token = "".join(random.sample(characters, 16))
        logging.debug(
            "No prior CONFIG ConfigMap exists for %s, assigning new token (%s)",
            cm_name,
            token,
        )
    assert token

    logging.info("Creating ConfigMaps %s...", name)

    # We must handle (and ignore) 409 exceptions with the objects we create
    # (with the reason 'Conflict'). This is interpreted as 'the object already exists'.
    # At this point we do know whether the 'config' ConfigMap exists,
    # so we don't need to create that again...

    config_vars = {"token": token, "base_url": name}
    config_cm_body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": cm_name, "labels": {"app": name}},
        "data": {json_data_key: _NOTEBOOK_CONFIG % config_vars},
    }
    kopf.adopt(config_cm_body)
    if not config_cm:
        # We create it because we know it does not exists.
        # No exception handling - any exceptions just get passed up to kopf...
        core_api.create_namespaced_config_map(
            namespace, config_cm_body, _request_timeout=_REQUEST_TIMEOUT
        )
        logging.debug("Created CONFIG ConfigMap %s", cm_name)

    cm_name = f"bp-{name}"
    bp_cm_body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": cm_name, "labels": {"app": name}},
        "data": {".bash_profile": _BASH_PROFILE},
    }
    kopf.adopt(bp_cm_body)
    try:
        core_api.create_namespaced_config_map(
            namespace, bp_cm_body, _request_timeout=_REQUEST_TIMEOUT
        )
        logging.debug("Created BP ConfigMap %s", cm_name)
    except kubernetes.client.exceptions.ApiException as ex:
        if ex.status != 409 or ex.reason != "Conflict":
            raise ex
        # Warn, but ignore and return a valid 'create' response now.
        logging.debug(
            "Got 409/Conflict creating BP ConfigMap %s. Ignoring - object already present",
            cm_name,
        )

    cm_name = f"startup-{name}"
    startup_cm_body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": cm_name, "labels": {"app": name}},
        "data": {"start.sh": _NOTEBOOK_STARTUP},
    }
    kopf.adopt(startup_cm_body)
    try:
        core_api.create_namespaced_config_map(
            namespace, startup_cm_body, _request_timeout=_REQUEST_TIMEOUT
        )
        logging.debug("Created STARTUP ConfigMap %s", cm_name)
    except kubernetes.client.exceptions.ApiException as ex:
        if ex.status != 409 or ex.reason != "Conflict":
            raise ex
        # Warn, but ignore and return a valid 'create' response now.
        logging.debug(
            "Got 409/Conflict creating STARTUP ConfigMap %s. Ignoring - object already present",
            cm_name,
        )

    # Deployment
    # ----------

    logging.info("Creating Deployment %s...", name)

    # Command is simply our custom start script,
    # which is mounted at /usr/local/bin
    command_items = ["bash", "/usr/local/bin/start.sh"]

    deployment_body: Dict[Any, Any] = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "labels": {"app": name}},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"deployment": name}},
            "strategy": {"type": "Recreate"},
            "template": {
                "metadata": {"labels": {"deployment": name}},
                "spec": {
                    "serviceAccountName": service_account,
                    "nodeSelector": {_POD_NODE_SELECTOR_KEY: _POD_NODE_SELECTOR_VALUE},
                    "containers": [
                        {
                            "name": "notebook",
                            "image": image,
                            "command": command_items,
                            "imagePullPolicy": image_pull_policy,
                            "resources": {
                                "requests": {
                                    "memory": memory_request,
                                    "cpu": cpu_request,
                                },
                                "limits": {"memory": memory_limit, "cpu": cpu_limit},
                            },
                            "ports": [
                                {
                                    "name": "8888-tcp",
                                    "containerPort": 8888,
                                    "protocol": "TCP",
                                }
                            ],
                            "env": [{"name": "HOME", "value": "/home/jovyan/." + name}],
                            "volumeMounts": [
                                {"name": "startup", "mountPath": "/usr/local/bin"},
                                {
                                    "name": "config",
                                    "mountPath": "/etc/jupyter_notebook_config.json",
                                    "subPath": "jupyter_notebook_config.json",
                                },
                                {
                                    "name": "bp",
                                    "mountPath": "/etc/.bash_profile",
                                    "subPath": ".bash_profile",
                                },
                                {
                                    "name": "project",
                                    "mountPath": "/home/jovyan",
                                    "subPath": project_id,
                                },
                            ],
                        }
                    ],
                    "securityContext": {
                        "runAsUser": sc_run_as_user,
                        "runAsGroup": sc_run_as_group,
                        "fsGroup": 100,
                    },
                    "volumes": [
                        {"name": "startup", "configMap": {"name": f"startup-{name}"}},
                        {"name": "bp", "configMap": {"name": f"bp-{name}"}},
                        {"name": "config", "configMap": {"name": f"config-{name}"}},
                        {
                            "name": "project",
                            "persistentVolumeClaim": {"claimName": project_claim_name},
                        },
                    ],
                },
            },
        },
    }

    # Insert a pod priority class?
    if _APPLY_POD_PRIORITY_CLASS:
        deployment_body["spec"]["template"]["spec"][
            "priorityClassName"
        ] = _DEFAULT_POD_PRIORITY_CLASS

    # Additional labels?
    #
    # If we find a key ending with '/owner', keep it
    # (for use as an environment variable later)
    instance_owner = "Unknown"
    for label in material.get("labels", []):
        key, value = label.split("=")
        deployment_body["spec"]["template"]["metadata"]["labels"][key] = value
        if key.endswith("/owner"):
            instance_owner = value

    # To simplify the dynamic ENV adjustments we're about to make...
    c_env = deployment_body["spec"]["template"]["spec"]["containers"][0]["env"]

    if notebook_interface != "classic":
        c_env.append({"name": "JUPYTER_ENABLE_LAB", "value": "true"})

    # Add a Project UUID environment variable
    c_env.append({"name": "DM_PROJECT_ID", "value": str(project_id)})
    # Add the instance owner (expected to have been extracted from a label)
    c_env.append({"name": "DM_INSTANCE_OWNER", "value": str(instance_owner)})

    apps_api = kubernetes.client.AppsV1Api()

    kopf.adopt(deployment_body)
    try:
        apps_api.create_namespaced_deployment(
            namespace, deployment_body, _request_timeout=_REQUEST_TIMEOUT
        )
        logging.debug("Created Deployment %s", name)
    except kubernetes.client.exceptions.ApiException as ex:
        if ex.status != 409 or ex.reason != "Conflict":
            raise ex
        # Warn, but ignore and return a valid 'create' response now.
        logging.debug(
            "Got 409/Conflict creating Deployment %s. Ignoring - object already present",
            name,
        )

    # Service
    # -------

    logging.info("Creating Service %s...", name)

    service_body = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "labels": {"app": name}},
        "spec": {
            "type": "ClusterIP",
            "ports": [
                {
                    "name": "8888-tcp",
                    "port": 8888,
                    "protocol": "TCP",
                    "targetPort": 8888,
                }
            ],
            "selector": {"deployment": name},
        },
    }

    kopf.adopt(service_body)
    try:
        core_api.create_namespaced_service(
            namespace, service_body, _request_timeout=_REQUEST_TIMEOUT
        )
        logging.debug("Created Service %s", name)
    except kubernetes.client.exceptions.ApiException as ex:
        if ex.status != 409 or ex.reason != "Conflict":
            raise ex
        # Warn, but ignore and return a valid 'create' response now.
        logging.debug(
            "Got 409/Conflict creating Service %s. Ignoring - object already present",
            name,
        )

    # Ingress
    # -------

    logging.info("Creating Ingress %s...", name)

    ingress_body: Dict[Any, Any] = {
        "kind": "Ingress",
        "apiVersion": "networking.k8s.io/v1",
        "metadata": {
            "name": name,
            "labels": {"app": name},
            "annotations": {
                "kubernetes.io/ingress.class": ingress_class,
                "nginx.ingress.kubernetes.io/proxy-body-size": f"{ingress_proxy_body_size}",
            },
        },
        "spec": {
            "tls": [{"hosts": [ingress_domain], "secretName": ingress_tls_secret}],
            "rules": [
                {
                    "host": ingress_domain,
                    "http": {
                        "paths": [
                            {
                                "path": ingress_path,
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {"name": name, "port": {"number": 8888}}
                                },
                            }
                        ]
                    },
                }
            ],
        },
    }

    # Inject the cert-manager annotation if a TLS secret is not defined
    # and a cert issuer is...
    if not ingress_tls_secret and ingress_cert_issuer:
        annotations = ingress_body["metadata"]["annotations"]
        annotations["cert-manager.io/cluster-issuer"] = ingress_cert_issuer

    ext_api = kubernetes.client.NetworkingV1Api()

    kopf.adopt(ingress_body)
    try:
        ext_api.create_namespaced_ingress(
            namespace, ingress_body, _request_timeout=_REQUEST_TIMEOUT
        )
        logging.debug("Created Ingress %s", name)
    except kubernetes.client.exceptions.ApiException as ex:
        if ex.status != 409 or ex.reason != "Conflict":
            raise ex
        # Warn, but ignore and return a valid 'create' response now.
        logging.debug(
            "Got 409/Conflict creating Ingress %s. Ignoring - object already present",
            name,
        )

    # Done
    # ----
    logging.info("Done %s (namespace=%s token=%s)", name, namespace, token)

    return {
        "notebook": {
            "url": f"http://{ingress_domain}{ingress_path}?token={token}",
            "token": token,
            "interface": notebook_interface,
        },
        "image": image,
        "serviceAccountName": service_account,
        "resources": {
            "requests": {"memory": memory_request},
            "limits": {"memory": memory_limit},
        },
        "project": {"claimName": project_claim_name, "id": project_id},
    }
