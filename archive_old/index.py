from llama_index.core import VectorStoreIndex,SimpleDirectoryReader,ServiceContext,PromptTemplate,Settings
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ✅ 明确指定 HuggingFace 嵌入模型，避免自动回退 OpenAI
embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")
Settings.embed_model = embed_model  # ✅ 设置为全局嵌入模型

# ✅ 读取记忆库目录
documents = SimpleDirectoryReader(
    input_dir=str(PROJECT_ROOT / "knowledge_base" / "saya" / "phase_0_preexistence")
).load_data()

# ✅ 构建索引（此处不再需要 embedding 参数）
index = VectorStoreIndex.from_documents(documents)

# ✅ 保存索引
index.storage_context.persist(
    persist_dir=str(PROJECT_ROOT / "knowledge_base" / "saya" / "phase_0_preexistence_index")
)

print("✅ 索引构建完成，已保存！")
