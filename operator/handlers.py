"""A kopf handler for the Jupyter CRD.
"""
import logging
import random
import os
import string
from typing import Any, Dict, Optional

import kubernetes
import kopf

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
_NOTEBOOK_STARTUP: str = """#!/bin/bash
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


@kopf.on.create("squonk.it", "v2", "jupyternotebooks", id="jupyter")
def create(spec: Dict[str, Any], name: str, namespace: str, **_: Any) -> Dict[str, Any]:
    """Handler for CRD create events.
    Here we construct the required Kubernetes objects,
    adopting them in kopf before using the corresponding Kubernetes API
    to create them.

    We handle errors typically raising 'kopf.PermanentError' to prevent
    Kubernetes constantly calling back for a given create.
    """

    characters = string.ascii_letters + string.digits
    token = "".join(random.sample(characters, 16))

    logging.info("Starting create (name=%s namespace=%s)...", name, namespace)
    logging.info("spec=%s (name=%s)", spec, name)

    # ConfigMaps
    # ----------

    logging.info("Creating ConfigMaps %s...", name)

    bp_cm_body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": f"bp-{name}", "labels": {"app": name}},
        "data": {".bash_profile": _BASH_PROFILE},
    }

    startup_cm_body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": f"startup-{name}", "labels": {"app": name}},
        "data": {"start.sh": _NOTEBOOK_STARTUP},
    }

    config_vars = {"token": token, "base_url": name}
    config_cm_body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": f"config-{name}", "labels": {"app": name}},
        "data": {"jupyter_notebook_config.json": _NOTEBOOK_CONFIG % config_vars},
    }

    kopf.adopt(bp_cm_body)
    kopf.adopt(startup_cm_body)
    kopf.adopt(config_cm_body)
    core_api = kubernetes.client.CoreV1Api()
    core_api.create_namespaced_config_map(namespace, bp_cm_body)
    core_api.create_namespaced_config_map(namespace, startup_cm_body)
    core_api.create_namespaced_config_map(namespace, config_cm_body)

    logging.info("Created ConfigMaps")

    # Deployment
    # ----------

    logging.info("Creating Deployment %s...", name)

    # All Data-Manager provided material
    # will be namespaced under the 'imDataManager' property
    material: Dict[str, Any] = spec.get("imDataManager", {})

    notebook_interface = material.get("notebook", {}).get("interface", "lab")

    image = material.get("image", _DEFAULT_IMAGE)
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
                            "imagePullPolicy": "IfNotPresent",
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

    kopf.adopt(deployment_body)
    apps_api = kubernetes.client.AppsV1Api()
    apps_api.create_namespaced_deployment(namespace, deployment_body)

    logging.info("Created deployment")

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
    core_api.create_namespaced_service(namespace, service_body)

    logging.info("Created service")

    # Ingress
    # -------

    logging.info("Creating Ingress %s...", name)

    ingress_proxy_body_size = material.get(
        "ingressProxyBodySize", _DEFAULT_INGRESS_PROXY_BODY_SIZE
    )

    ingress_class = material.get("ingressClass", _DEFAULT_INGRESS_CLASS)
    ingress_domain = material.get("ingressDomain", _DEFAULT_INGRESS_DOMAIN)
    ingress_tls_secret = material.get("ingressTlsSecret", _DEFAULT_INGRESS_TLS_SECRET)
    ingress_path = f"/{name}"

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

    kopf.adopt(ingress_body)
    ext_api = kubernetes.client.NetworkingV1Api()
    ext_api.create_namespaced_ingress(namespace, ingress_body)

    logging.info("Created ingress")

    # Done
    # ----

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
