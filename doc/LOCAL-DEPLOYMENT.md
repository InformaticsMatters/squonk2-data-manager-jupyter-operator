# Local deployment
Notes for deployment to a local cluster, like [minikube] or [Docker Desktop].

> It is assumed that you have a local Kubernetes cluster, built using
  minikube or Docker Desktop, **AND** you have already deployed the Data Manager
  the cluster.

You will now need: -

- This repository (but you'll have that already)
- Python 3
- [lens]

## Create an environment for the Ansible playbooks
You will need a Python virtual environment for ansible playbook execution.
You may be running playbooks from several repositories, so you can re-use
this one.

You must use Python 3: -

    python -m venv  ~/.venv/ansible

    source ~/.venv/ansible/bin/activate
    pip install wheel
    pip install -r requirements.txt

## Deploy the Jupyter Operator
From the root of your clone of the `data-manager-jupyter-operator` repository,
and within the Ansible environment you created in the previous step,
create a suitable Ansible parameter file called `parameters.yaml` using the
`parameters-template.yaml` file as a guide, replacing the `SetMe` lines.

>   You will need a KUBECONFIG file, and refer to it using the `jo_kubeconfig`
    variable and make sure `kubectl get no` returns nodes you expect.

Now deploy the Job Operator: -

    ansible-playbook site.yaml -e @parameters.yaml

>   You can check the deployment progress using [Lens].

---

[docker desktop]: https://www.docker.com/products/docker-desktop
[lens]: https://k8slens.dev
[minikube]: https://minikube.sigs.k8s.io/docs/start/
