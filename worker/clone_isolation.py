"""Fail-closed resource scope checks for AI Clone.

This module is deliberately independent of provider SDKs. Provider credentials must
also be resource-scoped; these checks are defense in depth, not a substitute for IAM.
"""
import os

class IsolationError(RuntimeError):
    pass

_SCOPE_ENV = {
    's3': 'AI_CLONE_S3_BUCKET',
    'drive': 'AI_CLONE_DRIVE_FOLDER_ID',
    'kaggle': 'AI_CLONE_KAGGLE_DATASET',
    'runpod': 'AI_CLONE_RUNPOD_NAMESPACE',
}

def assert_clone_resource(kind: str, resource: str) -> str:
    env_name = _SCOPE_ENV.get(kind)
    if env_name is None:
        raise IsolationError(f'unsupported resource kind: {kind}')
    expected = os.environ.get(env_name, '').strip()
    if not expected:
        raise IsolationError(f'{env_name} is required; refusing unscoped access')
    if not resource or resource.strip() != expected:
        raise IsolationError(f'resource denied by AI Clone isolation policy: {kind}')
    return resource
