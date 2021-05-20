import os
import random
import string

import kopf
import kubernetes

# Some (key) default deployment variables..
default_image = 'jupyter/minimal-notebook:notebook-6.3.0'
default_sa = 'default'
default_mem_limit = '512Mi'
default_user_id = 1000
default_group_id = 100

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

# A startup script.
# Used in a ConfigMap
# written into the directory '/usr/local/bin/before-notebook.d'
notebook_startup = """#!/bin/bash
conda init

source $HOME/.bashrc

if [ ! -f $HOME/.condarc ]; then
    cat > $HOME/.condarc << EOF
envs_dirs:
  - $HOME/.conda/envs
EOF
fi

if [ -d $HOME/.conda/envs/workspace ]; then
    echo "Activate virtual environment 'workspace'."
    conda activate workspace
fi
"""

# The Jupyter jupyter_notebook_config.json file.
# A ConfigMap written into the directory '$HOME/.jupyter'
notebook_config = """{
  "NotebookApp": {
    "token": "%(token)s",
    "base_url": "%(base_url)s"
  }
}
"""


@kopf.on.create("squonk.it", "v1alpha1", "jupyternotebooks", id="jupyter")
def create(name, uid, namespace, spec, logger, **_):

    characters = string.ascii_letters + string.digits
    token = "".join(random.sample(characters, 16))

    # ConfigMaps
    # ----------

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
            "setup-environment.sh": notebook_startup
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

    kopf.adopt(startup_cm_body)
    kopf.adopt(config_cm_body)
    core_api = kubernetes.client.CoreV1Api()
    core_api.create_namespaced_config_map(namespace, startup_cm_body)
    core_api.create_namespaced_config_map(namespace, config_cm_body)

    logger.debug("Created ConfigMaps")
    
    # Deployment
    # ----------

    notebook_interface = spec.get("notebook", {}).get("interface", "lab")

    image = spec.get("deployment", {}).get("image", default_image)
    service_account = spec.get("deployment", {})\
        .get("serviceAccountName", default_sa)

    memory_limit = spec.get("deployment", {}).get("resources", {})\
        .get("limits", {}).get("memory", default_mem_limit)
    memory_request = spec.get("deployment", {}).get("resources", {})\
        .get("requests", {}).get("memory", memory_limit)

    # Data Manager API compliance.
    #
    # The user and group IDs we're asked to run as.
    # The files in the container project volume will be owned
    # by this user and group. We must run as group 100.
    # We use the supplied group ID and pass that into the container
    # as the Kubernetes 'File System Group' (fsGroup).
    # This should allow us to run and manipulate the files.
    sc_run_as_user = spec.get("deployment", {}).get("securityContext", {})\
        .get("runAsUser", default_user_id)
    sc_run_as_group = spec.get("deployment", {}).get("securityContext", {})\
        .get("runAsGroup", default_group_id)

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
                        "deployment": name,
                        "data-manager.informaticsmatters.com/instance": name
                    }
                },
                "spec": {
                    "serviceAccountName": service_account,
                    "containers": [
                        {
                            "name": "notebook",
                            "image": image,
                            "imagePullPolicy": "Always",
                            "resources": {
                                "requests": {
                                    "memory": memory_request
                                },
                                "limits": {
                                    "memory": memory_limit
                                }
                            },
                            "ports": [
                                {
                                    "name": "8888-tcp",
                                    "containerPort": 8888,
                                    "protocol": "TCP",
                                }
                            ],
                            "env": [],
                            "volumeMounts": [
                                {
                                    "name": "startup",
                                    "mountPath": "/usr/local/bin/before-notebook.d"
                                },
                                {
                                    "name": "config",
                                    "mountPath": "/home/jovyan/.jupyter/jupyter_notebook_config.json",
                                    "subPath": "jupyter_notebook_config.json"
                                }
                            ]
                        }
                    ],
                    "securityContext": {
                        "runAsUser": sc_run_as_user,
                        "runAsGroup": 100,
                        "fsGroup": sc_run_as_group
                    },
                    "volumes": [
                        {
                            "name": "startup",
                            "configMap": {
                                "name": "startup-%s" % name
                            }
                        },
                        {
                            "name": "config",
                            "configMap": {
                                "name": "config-%s" % name
                            }
                        }
                    ]
                },
            },
        },
    }

    # To simplify the dynamic adjustments we're about to make...
    deployment_spec = deployment_body["spec"]["template"]["spec"]

    if notebook_interface != "classic":
        deployment_spec["containers"][0]["env"].append({"name": "JUPYTER_ENABLE_LAB",
                                                        "value": "true"})

    storage_claim_name = spec.get("storage", {}).get("claimName", "")
    storage_sub_path = spec.get("storage", {}).get("subPath", "")

    volume = {"name": "data",
              "persistentVolumeClaim": {"claimName": storage_claim_name}}
    deployment_spec["volumes"].append(volume)

    storage_mount = {"name": "data",
                     "mountPath": "/home/jovyan",
                     "subPath": storage_sub_path}
    deployment_spec["containers"][0]["volumeMounts"].append(storage_mount)

    kopf.adopt(deployment_body)
    apps_api = kubernetes.client.AppsV1Api()
    apps_api.create_namespaced_deployment(namespace, deployment_body)

    logger.debug("Created deployment")

    # Service
    # -------

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
        "deployment": {
            "image": image,
            "serviceAccountName": service_account,
            "resources": {
                "requests": {
                    "memory": memory_request
                },
                "limits": {
                    "memory": memory_limit
                }
            }
        },
        "storage": {
            "claimName": storage_claim_name,
            "subPath": storage_sub_path
        }
    }


@kopf.on.delete("squonk.it", "v1alpha1", "jupyternotebooks")
def delete(body, **kwargs): 
    msg = f"Jupyter notebook {body['metadata']['name']} deleted"
    return {'message': msg}
