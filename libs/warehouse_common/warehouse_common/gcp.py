"""Google Cloud clients.

On GKE they authenticate through Workload Identity, so no key file exists anywhere.
Locally, STORAGE_EMULATOR_HOST and FIRESTORE_EMULATOR_HOST point them at emulators.
"""
from __future__ import annotations

import os

from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore, storage

from warehouse_common.settings import Settings


def data_bucket(settings: Settings) -> storage.Bucket:
    if os.environ.get("STORAGE_EMULATOR_HOST"):
        client = storage.Client(project=settings.gcp_project, credentials=AnonymousCredentials())
    else:
        client = storage.Client(project=settings.gcp_project)
    return client.bucket(settings.data_bucket)


def firestore_client(settings: Settings) -> firestore.Client:
    return firestore.Client(project=settings.gcp_project, database=settings.firestore_database)
