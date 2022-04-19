#!/usr/bin/env bash
NS=$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace)
kopf run ./handlers.py --verbose --standalone --namespace=${NS} --log-format full
