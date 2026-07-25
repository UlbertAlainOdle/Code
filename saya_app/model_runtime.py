import gc

from .config import DEFAULT_LOCAL_MODEL, MODEL_CONFIGS


class LocalModelDependencyError(RuntimeError):
    pass


def load_llama_class():
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise LocalModelDependencyError(
            "Local GGUF models require llama-cpp-python. "
            "Install it only if you want to use local models: "
            "python -m pip install llama-cpp-python"
        ) from exc
    return Llama


class ModelRuntime:
    def __init__(self, configs=None):
        self.configs = configs or MODEL_CONFIGS
        self.loaded_models = {}
        self.current_model_name = None

    def unload_models(self):
        if not self.loaded_models:
            return
        print("Unloading previous model to free VRAM...")
        for key in list(self.loaded_models.keys()):
            model = self.loaded_models.pop(key)
            del model
        gc.collect()
        print("VRAM cleanup attempted.")

    def set_api_mode(self):
        self.unload_models()
        self.current_model_name = "api"

    def get_model(self, model_name=DEFAULT_LOCAL_MODEL):
        if model_name == "api":
            self.set_api_mode()
            return None

        if model_name not in self.configs:
            print(f"Unknown model: {model_name}, falling back to {DEFAULT_LOCAL_MODEL}")
            model_name = DEFAULT_LOCAL_MODEL

        config = self.configs[model_name]
        target_path = config["path"]
        params = config["params"]

        if model_name in self.loaded_models:
            self.current_model_name = model_name
            return self.loaded_models[model_name]

        Llama = load_llama_class()
        self.unload_models()

        print(f"Loading model: {model_name} from {target_path}...")
        try:
            init_params = params.copy()
            init_params["model_path"] = target_path
            instance = Llama(**init_params)
        except Exception as exc:
            print(f"Error loading model {model_name}: {exc}")
            raise

        self.loaded_models[model_name] = instance
        self.current_model_name = model_name
        return instance


model_runtime = ModelRuntime()


def get_model(model_name=DEFAULT_LOCAL_MODEL):
    return model_runtime.get_model(model_name)
