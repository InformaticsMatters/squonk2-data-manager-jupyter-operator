import os
import random
import string
from typing import Dict

import kopf
import kubernetes

# Some (key) default deployment variables...
default_image = 'jupyter/minimal-notebook:notebook-6.3.0'
default_sa = 'default'
default_cpu_limit = '1'
default_cpu_request = '10m'
default_mem_limit = '1Gi'
default_mem_request = '256Mi'
default_user_id = 1000
default_group_id = 100
default_ingress_proxy_body_size = '500m'

# The ingress class
ingress_class = 'nginx'
# The ingress domain must be provided.
ingress_domain = os.environ['INGRESS_DOMAIN']
# The ingress TLS secret is optional.
# If provided it is used as the Ingress secret
# and cert-manager is avoided.
ingress_tls_secret = os.environ.get('INGRESS_TLS_SECRET')
# The cert-manager issuer,
# expected if a TLS certificate is not defined.
ingress_cert_issuer = os.environ.get('INGRESS_CERT_ISSUER')

# Application node selection
_POD_NODE_SELECTOR_KEY: str = os.environ\
    .get('JO_POD_NODE_SELECTOR_KEY', 'informaticsmatters.com/purpose-worker')
_POD_NODE_SELECTOR_VALUE: str = os.environ\
    .get('JO_POD_NODE_SELECTOR_VALUE', 'yes')

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
notebook_startup = """#!/bin/bash
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
bash_profile = """if [ -f ~/.bashrc ]; then
    source ~/.bashrc
fi
"""

# The Jupyter jupyter_notebook_config.json file.
# A ConfigMap whose content is written into '/etc'
# and copied to the $HOME/.jupyter by the notebook_startup
# script (above).
notebook_config = """{
  "NotebookApp": {
    "token": "%(token)s",
    "base_url": "%(base_url)s"
  }
}
"""


@kopf.on.create("squonk.it", "v1alpha3", "jupyternotebooks", id="jupyter")
def create(name, uid, namespace, spec, logger, **_):

    characters = string.ascii_letters + string.digits
    token = "".join(random.sample(characters, 16))

    logging.info('Starting create (name=%s namespace=%s)...', name, namespace)
    logging.info('spec=%s (name=%s)', spec, name)

    # ConfigMaps
    # ----------

    logging.info('Creating ConfigMaps %s...', name)

    bp_cm_body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "bp-%s" % name,
            "labels": {
                "app": name
            }
        },
        "data": {
            ".bash_profile": bash_profile
        }
    }

    startup_cm_body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "startup-%s" % name,
            "labels": {
                "app": name
            }
        },
        "data": {
            "start.sh": notebook_startup
        }
    }

    config_vars = {'token': token,
                   'base_url': name}
    config_cm_body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "config-%s" % name,
            "labels": {
                "app": name
            }
        },
        "data": {
            "jupyter_notebook_config.json": notebook_config % config_vars
        }
    }

    kopf.adopt(bp_cm_body)
    kopf.adopt(startup_cm_body)
    kopf.adopt(config_cm_body)
    core_api = kubernetes.client.CoreV1Api()
    core_api.create_namespaced_config_map(namespace, bp_cm_body)
    core_api.create_namespaced_config_map(namespace, startup_cm_body)
    core_api.create_namespaced_config_map(namespace, config_cm_body)

    logger.debug("Created ConfigMaps")
    
    # Deployment
    # ----------

    logging.info('Creating Deployment %s...', name)

    # All Data-Manager provided material
    # will be namespaced under the 'imDataManager' property
    material: Dict[str, any] = spec.get('imDataManager', {})

    notebook_interface = material.get("notebook", {}).get("interface", "lab")

    image = material.get("image", default_image)
    service_account = material.get("serviceAccountName", default_sa)

    resources = material.get("resources", {})
    cpu_limit = resources.get("limits", {}).get("cpu", default_cpu_limit)
    cpu_request = resources.get("requests", {}).get("cpu", default_cpu_request)
    memory_limit = resources.get("limits", {}).get("memory", default_mem_limit)
    memory_request = resources.get("requests", {}).get("memory", default_mem_request)

    task_id: str = material.get('taskId')

    # Data Manager API compliance.
    #
    # The user and group IDs we're asked to run as.
    # The files in the container project volume will be owned
    # by this user and group. We must run as group 100.
    # We use the supplied group ID and pass that into the container
    # as the Kubernetes 'File System Group' (fsGroup).
    # This should allow us to run and manipulate the files.
    sc_run_as_user = material.get("securityContext", {}).get("runAsUser", default_user_id)
    sc_run_as_group = material.get("securityContext", {}).get("runAsGroup", default_group_id)

    # Project storage
    project_claim_name = material.get("project", {}).get("claimName")
    project_id = material.get("project", {}).get("id")

    # Command is simply our custom start script,
    # which is mounted at /usr/local/bin
    command_items = ['bash', '/usr/local/bin/start.sh']

    deployment_body = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "labels": {
                "app": name
            }
        },
        "spec": {
            "replicas": 1,
            "selector": {
                "matchLabels": {
                    "deployment": name
                }
            },
            "strategy": {
                "type": "Recreate"
            },
            "template": {
                "metadata": {
                    "labels": {
                        "deployment": name
                    }
                },
                "spec": {
                    "serviceAccountName": service_account,
                    'nodeSelector': {
                        _POD_NODE_SELECTOR_KEY: _POD_NODE_SELECTOR_VALUE
                    },
                    "containers": [
                        {
                            "name": "notebook",
                            "image": image,
                            "command": command_items,
                            "imagePullPolicy": "IfNotPresent",
                            "resources": {
                                "requests": {
                                    "memory": memory_request,
                                    "cpu": cpu_request
                                },
                                "limits": {
                                    "memory": memory_limit,
                                    "cpu": cpu_limit
                                }
                            },
                            "ports": [
                                {
                                    "name": "8888-tcp",
                                    "containerPort": 8888,
                                    "protocol": "TCP",
                                }
                            ],
                            "env": [
                                {
                                    "name": "HOME",
                                    "value": "/home/jovyan/." + name
                                }
                            ],
                            "volumeMounts": [
                                {
                                    "name": "startup",
                                    "mountPath": "/usr/local/bin"
                                },
                                {
                                    "name": "config",
                                    "mountPath": "/etc/jupyter_notebook_config.json",
                                    "subPath": "jupyter_notebook_config.json"
                                },
                                {
                                    "name": "bp",
                                    "mountPath": "/etc/.bash_profile",
                                    "subPath": ".bash_profile"
                                },
                                {
                                    "name": "project",
                                    "mountPath": "/home/jovyan",
                                    "subPath": project_id
                                }
                            ]
                        }
                    ],
                    "securityContext": {
                        "runAsUser": sc_run_as_user,
                        "runAsGroup": sc_run_as_group,
                        "fsGroup": 100
                    },
                    "volumes": [
                        {
                            "name": "startup",
                            "configMap": {
                                "name": "startup-%s" % name
                            }
                        },
                        {
                            "name": "bp",
                            "configMap": {
                                "name": "bp-%s" % name
                            }
                        },
                        {
                            "name": "config",
                            "configMap": {
                                "name": "config-%s" % name
                            }
                        },
                        {
                            "name": "project",
                            "persistentVolumeClaim": {
                                "claimName": project_claim_name
                            }
                        }
                    ]
                },
            },
        },
    }

    # Additional labels?
    # Provided by the DM as an array of strings of the form '<KEY>=<VALUE>'
    for label in material.get("labels", []):
        key, value = label.split("=")
        deployment_body["spec"]["template"]["metadata"]["labels"][key] = value

    # To simplify the dynamic ENV adjustments we're about to make...
    c_env = deployment_body["spec"]["template"]["spec"]["containers"][0]["env"]

    if notebook_interface != "classic":
        c_env.append({"name": "JUPYTER_ENABLE_LAB",
                      "value": "true"})

    kopf.adopt(deployment_body)
    apps_api = kubernetes.client.AppsV1Api()
    apps_api.create_namespaced_deployment(namespace, deployment_body)

    logger.debug("Created deployment")

    # Service
    # -------

    logger.debug("Creating Service %s...", name)

    service_body = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "labels": {
                "app": name
            }
        },
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
            "selector": {
                "deployment": name
            },
        },
    }

    kopf.adopt(service_body)
    core_api.create_namespaced_service(namespace, service_body)

    logger.debug("Created service")

    # Ingress
    # -------

    logger.debug("Creating Ingress %s...", name)

    ingress_proxy_body_size = material.get("ingressProxyBodySize", default_ingress_proxy_body_size)

    ingress_path = f"/{name}"
    tls_secret = ingress_tls_secret if ingress_tls_secret else f"{name}-tls"

    ingress_body = {
        "kind": "Ingress",
        "apiVersion": "extensions/v1beta1",
        "metadata": {
            "name": name,
            "labels": {
                "app": name
            },
            "annotations": {
                "kubernetes.io/ingress.class": ingress_class,
                "nginx.ingress.kubernetes.io/proxy-body-size": f"{ingress_proxy_body_size}"
            }
        },
        "spec": {
            "tls": [
                {
                    "hosts": [
                        ingress_domain
                    ],
                    "secretName": tls_secret
                }
            ],
            "rules": [
                {
                    "host": ingress_domain,
                    "http": {
                        "paths": [
                            {
                                "path": ingress_path,
                                "backend": {
                                    "serviceName": name,
                                    "servicePort": 8888,
                                },
                            }
                        ]
                    }
                }
            ]
        }
    }

    # Inject the cert-manager annotation if a TLS secret is not defined
    # and a cert issuer is...
    if not ingress_tls_secret and ingress_cert_issuer:
        annotations = ingress_body['metadata']['annotations']
        annotations["cert-manager.io/cluster-issuer"] = ingress_cert_issuer

    kopf.adopt(ingress_body)
    ext_api = kubernetes.client.ExtensionsV1beta1Api()
    ext_api.create_namespaced_ingress(namespace, ingress_body)

    logger.debug("Created ingress")

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
            "requests": {
                "memory": memory_request
            },
            "limits": {
                "memory": memory_limit
            }
        },
        "project": {
            "claimName": project_claim_name,
            "id": project_id
        }
    }


@kopf.on.delete("squonk.it", "v1alpha3", "jupyternotebooks")
def delete(body, **kwargs): 
    msg = f"Jupyter notebook {body['metadata']['name']} deleted"
    return {'message': msg}
