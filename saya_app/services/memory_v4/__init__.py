"""Version 4 memory contracts and persistence.

The package is deliberately independent from the legacy ``KnowledgeBase``.
Stage 1 creates a safe seam for migration and shadow writes; callers must opt
in before V4 data can affect answers.
"""

from .models import (
    DayState,
    MemoryCandidateV4,
    MemoryEvidence,
    MemoryItemV4,
    MemoryProposal,
    MemoryVersion,
    TurnEvent,
    ValidationError,
    validate_memory_item,
)
from .store import MemoryV4Store
from .migration import MemoryV4Migration, MigrationReport
from .compat import DualWriteCoordinator
from .flags import FEATURE_FLAG_DEFAULTS, FeatureFlagStore
from .write_pipeline import (
    CollectedEvidence,
    DeterministicValidator,
    EvidenceCollector,
    ImportancePolicy,
    StructuredExtractor,
    StructuredWriteProposal,
    ValidationResult,
    WriteIntent,
    WriteIntentDecision,
    WriteIntentGate,
    WritePipeline,
)
from .retrieval import (
    HybridMemoryRetriever,
    MemoryCandidateSummary,
    MemoryNeedPlanner,
    MemoryQuery,
    MemoryRetriever,
    RetrievalResult,
)
from .daily_state import DayStateManager
from .prompt_compiler import PromptBlock, PromptCompileResult, PromptCompiler, PromptCompilerV4, needs_time_context
from .capabilities import Capability, CapabilityPolicy, CapabilityRegistry, register_memory_capabilities
from .agent_loop import AgentLoop, AgentLoopResult
from .web_tools import SourceRegistry, WebToolBudget, WebToolsV2
from .connectors import (
    AttentionInbox, ConnectorEvent, ConnectorPolicy, DiscordReadOnlyConnector,
    HttpReadOnlyConnector, ReadOnlyConnector, XReadOnlyConnector,
)
from .model_adapters import (
    AgentModelSettings, LocalLlamaAgentStep, ModelAdapterError,
    OpenAICompatibleAgentStep, build_agent_messages, normalize_agent_model_output,
)
from .synthesis import BackgroundSynthesizer, BackgroundSynthesisScheduler, ExternalWriteGate, MemoryControls, SynthesisPatch, TemporaryChat

__all__ = [
    "DayState",
    "DayStateManager",
    "DualWriteCoordinator",
    "CollectedEvidence",
    "AgentLoop",
    "AgentLoopResult",
    "AttentionInbox",
    "Capability",
    "CapabilityPolicy",
    "CapabilityRegistry",
    "ConnectorEvent",
    "ConnectorPolicy",
    "DiscordReadOnlyConnector",
    "HttpReadOnlyConnector",
    "XReadOnlyConnector",
    "BackgroundSynthesizer",
    "BackgroundSynthesisScheduler",
    "ExternalWriteGate",
    "MemoryControls",
    "DeterministicValidator",
    "EvidenceCollector",
    "FEATURE_FLAG_DEFAULTS",
    "FeatureFlagStore",
    "HybridMemoryRetriever",
    "ImportancePolicy",
    "MemoryCandidateV4",
    "MemoryCandidateSummary",
    "MemoryEvidence",
    "MemoryItemV4",
    "MemoryNeedPlanner",
    "MemoryQuery",
    "MemoryRetriever",
    "MemoryProposal",
    "MemoryV4Migration",
    "MemoryV4Store",
    "MemoryVersion",
    "MigrationReport",
    "PromptBlock",
    "PromptCompileResult",
    "PromptCompiler",
    "PromptCompilerV4",
    "RetrievalResult",
    "ReadOnlyConnector",
    "SynthesisPatch",
    "TemporaryChat",
    "register_memory_capabilities",
    "StructuredExtractor",
    "StructuredWriteProposal",
    "TurnEvent",
    "ValidationError",
    "ValidationResult",
    "WriteIntent",
    "WriteIntentDecision",
    "WriteIntentGate",
    "WritePipeline",
    "SourceRegistry",
    "WebToolBudget",
    "WebToolsV2",
    "AgentModelSettings",
    "LocalLlamaAgentStep",
    "ModelAdapterError",
    "OpenAICompatibleAgentStep",
    "build_agent_messages",
    "normalize_agent_model_output",
    "needs_time_context",
    "validate_memory_item",
]
