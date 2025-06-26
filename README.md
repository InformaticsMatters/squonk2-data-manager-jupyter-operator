# A Jupyter Application Operator (for the Data Manager API)

[![Data Manager: Application](https://img.shields.io/badge/squonk2%20data%20manager-application-000000?labelColor=dc332e)]()
[![Dev Stage: 1](https://img.shields.io/badge/dev%20stage-★☆☆%20%281%29-000000?labelColor=dc332e)](https://github.com/InformaticsMatters/code-repository-development-stages)

![Architecture](https://img.shields.io/badge/architecture-amd64%20%7C%20arm64-lightgrey)

[![build](https://github.com/informaticsmatters/squonk2-data-manager-jupyter-operator/actions/workflows/build.yaml/badge.svg)](https://github.com/informaticsmatters/squonk2-data-manager-jupyter-operator/actions/workflows/build.yaml)
[![build latest](https://github.com/informaticsmatters/squonk2-data-manager-jupyter-operator/actions/workflows/build-latest.yaml/badge.svg)](https://github.com/informaticsmatters/squonk2-data-manager-jupyter-operator/actions/workflows/build-latest.yaml)
[![build tag](https://github.com/informaticsmatters/squonk2-data-manager-jupyter-operator/actions/workflows/build-tag.yaml/badge.svg)](https://github.com/informaticsmatters/squonk2-data-manager-jupyter-operator/actions/workflows/build-tag.yaml)
[![build stable](https://github.com/informaticsmatters/squonk2-data-manager-jupyter-operator/actions/workflows/build-stable.yaml/badge.svg)](https://github.com/informaticsmatters/squonk2-data-manager-jupyter-operator/actions/workflows/build-stable.yaml)

![GitHub](https://img.shields.io/github/license/informaticsmatters/squonk2-data-manager-jupyter-operator)

![GitHub tag (latest SemVer pre-release)](https://img.shields.io/github/v/tag/informaticsmatters/squonk2-data-manager-jupyter-operator?include_prereleases)

[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow.svg)](https://conventionalcommits.org)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

This repo contains a Kubernetes _Operator_ based on the [kopf] and [kubernetes]
Python packages that is used by the **Informatics Matters Squonk2 Data Manager API**
to create Jupyter Notebooks for the Data Manager service.

The operator's Custom Resource Definition (CRD) can be found in
`roles/operator/files`.

By default, the operator creates instances using the Jupyter image: -

-   `jupyter/minimal-notebook:notebook-6.3.0` (see `handlers.py`)

Prerequisites: -

-   Python
-   Docker
-   A kubernetes config file
-   A compatible Kubernetes (e.g. 1.22 thru 1.24 if the operator is built for 1.23)

## Contributing
The project uses: -

- [pre-commit] to enforce linting of files prior to committing them to the
  upstream repository
- [Commitizen] to enforce a [Conventional Commit] commit message format
- [Black] as a code formatter

You **MUST** comply with these choices in order to  contribute to the project.

To get started review the pre-commit utility and the conventional commit style
and then set-up your local clone by following the **Installation** and
**Quick Start** sections: -

    pip install -r build-requirements.txt
    pre-commit install -t commit-msg -t pre-commit

Now the project's rules will run on every commit, and you can check the
current health of your clone with: -

    pre-commit run --all-files

## Building the operator (local development)
Pre-requisites: -

- Docker Compose (v2)

The operator container, residing in the `operator` directory,
is automatically built and pushed to Docker Hub using GitHub Actions.

You can build and push the image yourself using docker-compose.
The following will build an operator image with a specific tag: -

    export IMAGE_TAG=23.1.0-alpha.1
    docker compose build
    docker compose push

## Deploying into the Data Manager API
We use [Ansible] 3 and community modules in [Ansible Galaxy] as the deployment
mechanism, using the `operator` Ansible role in this repository and a
Kubernetes config (KUBECONFIG). All of this is done via a suitable Python
environment using the requirements in the root of the project...

    python -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    ansible-galaxy install -r requirements.yaml

Set your KUBECONFIG for the cluster and verify its right: -

    export KUBECONFIG=~/k8s-config/local-config
    kubectl get no
    [...]

Now, create a parameter file (i.e. `parameters.yaml`) based on the project's
`example-parameters.yaml`, setting values for the operator that match your
needs. Then deploy, using Ansible, from the root of the project: -

    export PARAMS=parameters
    ansible-playbook -e @${PARAMS}.yaml site.yaml

That deploys the operator and its CRD to your chosen operator namespace.
To deploy the Data Manager RBAC and Jupyter notebook configuration objects
you need to run the `site_dm.yaml` playbook: -

    ansible-playbook -e @${PARAMS}.yaml site_dm.yaml

>   If deploying to multiple Data Managers you should just need one operator
    and then deploy RBACs to each DM namespace. Remember to also adjust the
    annotations of for CRD so each DM namespace recognises it as a valid
    application.

To remove the operator (assuming there are no operator-derived instances)...

    ansible-playbook -e @${PARAMS}.yaml -e jo_state=absent site.yaml

>   The current Data Manager API assumes that once an Application (operator)
    has been installed it is not removed. So, removing the operator here
    is described simply to illustrate a 'clean-up' - you would not
    normally remove an Application operator in a production environment.

### Deploying to the official cluster
The parameters used to deploy the operator to our 'official' cluster
are held in this repository.

To deploy the operator itself run the main 'site' playbook with
a suitable set of parameters: -

    export KUBECONFIG=~/k8s-config/config-aws-im-main-eks
    export PARAMS=staging
    ansible-playbook -e @${PARAMS}-parameters.yaml site.yaml

Then, you must run the `site_dm` playbook to for each Data Manager
you wish to configure: -

    ansible-playbook -e xchem-dev-integration-parameters.yaml site_dm.yaml

    ansible-playbook -e xchem-dev-test-parameters.yaml site_dm.yaml

This will install the RBAC and configuration objects for Jupyter
to the corresponding DM namespaces.

# Data Manager Application Compliance
In order to expose the CRD as an _Application_ in the Data Manager API service
you will need to a) annotate the CRD and b) provide a **Role** and
**RoleBinding**.

## Custom Resource Definition (CRD) annotations
For the **CRD** to be recognised by the Data Manager API it wil need a number of
annotations, located in its `metadata -> annotations` block.
You will need: -

-   An annotation `data-manager.informaticsmatters.com/application`
    set to `'yes'`
-   An annotation `data-manager.informaticsmatters.com/application-namespaces`
    set to a colon-separated list of namespaces the Application is to be used
    in. e.g `'data-manager-api:data-manager-api-staging'`
-   An annotation `data-manager.informaticsmatters.com/application-url-location`.
    The url location is the 'status'-relative path in the custom resource
    'status' block where the application URL can be extracted. A value of
    `jupyter.notebook.url` would imply that the Application URL
    can be found in the custom resource object using the Python dictionary
    reference: `custom_resource['status']['jupyter']['notebook']['url']`.

>   Our CRD already contains suitable annotations
    (see `roles/operator/files/crd.yaml`), so there's nothing more to
    do here once you've deployed it (using Ansible in our case,
    as described earlier).

## Pod labels
So that **Pod** instances can be recognised by the Data Manager API the
application's **Pod** (only one if there are many) must contain the following
label: -

    data-manager.informaticsmatters.com/instance

Which must have a value that matched the `name` given to the operator
by the Data Manager. The name is a unique reference for the application
instance.

>   See the `spec.template.metadata.labels` block in the `deployment_body`
    section of the `create()` function in our `operator/handlers.py`.

## Role and RoleBinding definitions
As well as providing RBAC for the Operator you will need a **Role** and
**RoleBinding** to allow the Data Manager to execute the Operator. These must
allow the Data Manager to launch instances of the Custom Resource in the
Data Manager's **Namespace**.

Typical **Role** and **RoleBinding** definitions are provided in this
repository. Once you define yours you'll just need to create them: -

    kubectl create -f data-manager-rbac.yaml

With this done the application should be visible through the Data Manager API's
**/application** REST endpoint.

## Security context
The Custom Resource must expose properties that allow a custom
**SecurityContext** to be applied. If not, the application instance will not be
able to access the Data Manager Project files. The Data-Manager API will
expect to provide the following properties through the **CRD** schema's: -

-   `spec.securityContext.runAsUser`
-   `spec.securityContext.runAsGroup`

To run successfully the container must be able to run without privileges
and run using a user and group that is assigned by the Data Manager API.

>   See our handling of these values in the `create()` function
    of our `operator/handlers.py` and their definitions
    in `roles/operator/files/crd.yaml`

## Storage volume
In order to place Data-Manager Project files the **CRD** must
expose the following properties through its schema's: -

-   `spec.project.claimName`
-   `spec.project.id`

These will be expected to provide a suitable volume mount within the
application **Pod** for the Project files.

>   See our use of these values in `roles/operator/files/crd.yaml`.

## Instance certificate variables
Applications can use the DM-API ingress, if they use path-based routing,
and are happy to share the DM-API domain. Doing this means you won't need
a separate TLS certificate, instead using the Data Manager's.

The Jupyter operator supports this vis a Pod environment variable that is
set if you provide a value for the Ansible playbook variable
`jo_ingress_tls_secret`. If left blank the operator will expect to use the
Kubernetes [Certificate Manager], where you are expected to provide the
certificate issuer name using the playbook variable `jo_ingress_cert_issuer`.

Both are exposed in the example parameter file `example-parameters.yaml`.

## Populating the home directory
A number of key files are prepared by the built-in `/usr/local/bin/start.sh` script
that the operator creates (via a **ConfigMap**). This script, used as the
container's **command**, will also recursively copy the content of the container image's
`/home/code/copy-to-home` directory (if it exists) to `~` prior to running Jupyter.
The script copies files using the command: -

    cp -r -u /home/code/copy-to-home/* ~

---

[ansible]: https://www.ansible.com
[ansible galaxy]: https://galaxy.ansible.com
[black]: https://black.readthedocs.io/en/stable
[certificate manager]: https://cert-manager.io/docs/installation/kubernetes/
[commitizen]: https://commitizen-tools.github.io/commitizen/
[conventional commit]: https://www.conventionalcommits.org/en/v1.0.0/
[kopf]: https://pypi.org/project/kopf/
[kubernetes]: https://pypi.org/project/kubernetes/
[pre-commit]: https://pre-commit.com
