#!/usr/bin/env bash
kopf run ./handlers.py --standalone --all-namespaces --log-format full ${KOPF_EXTRA_OPTIONS}
