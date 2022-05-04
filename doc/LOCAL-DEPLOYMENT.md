# Local deployment
Notes for deployment to a local cluster, like [minikube] or [Docker Desktop].

> It is assumed that you have a local Kubernetes cluster, built using
  minikube or Docker Desktop, **AND** you have already deployed the Data Manager
  the cluster.

You will now need: -

- This repository (but you'll have that already)
- Python 3
- A [kubectl] that matches your cluster

## Create an environment for the Ansible playbooks
You will need a Python virtual environment for ansible playbook execution.
You may be running playbooks from several repositories, so you can re-use
this one.

You must use Python 3: -

    python -m venv venv

    source venv/bin/activate
    pip install -r requirements.txt

## Deploy the Jupyter Operator
From the root of your clone of the `data-manager-jupyter-operator` repository,
and within the Ansible environment you created in the previous step,
copy the `local-parameters.yaml` file to `parameters.yaml` and change the variables
to suit your local cluster.

>   You will need a KUBECONFIG file, and refer to it using the `jo_kubeconfig`
    variable and make sure `kubectl get no` returns nodes you expect.

Now deploy the Job Operator: -

    ansible-playbook site.yaml -e @parameters.yaml

The operator should deploy to the namespace `data-manager-jupyter-operator`.
Run: -

    kubectl get po -n data-manager-jupyter-operator

To see something like this...

    NAME                                READY   STATUS    RESTARTS   AGE
    jupyter-operator-6895bc77f9-9pg44   1/1     Running   0          35s

---

[docker desktop]: https://www.docker.com/products/docker-desktop
[kubectl]: https://kubernetes.io/docs/tasks/tools
[minikube]: https://minikube.sigs.k8s.io/docs/start
