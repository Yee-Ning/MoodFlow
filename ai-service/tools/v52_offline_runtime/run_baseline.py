"""Run the frozen V5.2 H2 baseline without consulting Hugging Face credentials.

New huggingface_hub releases probe the cached token even in offline mode.
Public pinned local models need no token. Deny that lookup at its API boundary;
the original process audit guard, model settings and all retrieval stay intact.
"""

from contextlib import contextmanager
import sys
from unittest.mock import patch


@contextmanager
def anonymous_local_models():
    import huggingface_hub
    import huggingface_hub.constants
    import huggingface_hub.utils
    from huggingface_hub.utils import _auth, _headers

    with (
        patch.object(huggingface_hub.constants, "HF_HUB_OFFLINE", True),
        patch.object(huggingface_hub.constants, "HF_HUB_DISABLE_IMPLICIT_TOKEN", True),
        patch.object(huggingface_hub, "get_token", return_value=None),
        patch.object(huggingface_hub.utils, "get_token", return_value=None),
        patch.object(_auth, "get_token", return_value=None),
        patch.object(_headers, "get_token", return_value=None),
    ):
        yield


def main():
    from evals.rag_h2_metadata_v5 import configure

    # Set offline flags before importing libraries which cache environment flags.
    configure()
    from evals import rag_v52_h2_baseline as baseline

    if len(sys.argv) < 2 or sys.argv[1] != "run":
        raise ValueError("offline_wrapper_only_accepts_frozen_retrieval_run")
    with anonymous_local_models():
        return baseline.main()


if __name__ == "__main__":
    raise SystemExit(main())
