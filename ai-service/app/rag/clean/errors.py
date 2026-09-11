"""Expected failures raised by the offline Clean pipeline."""


class CleanPipelineError(Exception):
    """Base class for clean-stage validation and execution failures."""


class NormalizedCorpusError(CleanPipelineError):
    """The normalized corpus ledger or a referenced document is invalid."""


class CleanConfigError(CleanPipelineError):
    """The versioned Clean configuration is invalid."""


class RuleRegistryError(CleanPipelineError):
    """The rule registry is invalid or internally inconsistent."""


class NoiseDiscoveryError(CleanPipelineError):
    """Noise discovery could not validate or complete an LLM analysis batch."""
