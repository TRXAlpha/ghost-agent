<div align="center">

# ghost-agent
### Local-First AI Coding Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-yellow.svg)](https://www.python.org/)
[![Privacy](https://img.shields.io/badge/Privacy-100%25_Local-red.svg)]()

*A free, privacy-focused alternative to Codex/Copilot. No telemetry. No subscriptions. Just code.*

[**Report Bug**](../../issues) · [**Request Feature**](../../issues)

</div>

---

## 💀 The Mission

**Ghost Agent** is a response to the closed-source, data-harvesting nature of modern corporate AI tools. 

While big tech companies lock their "Codex" models behind paywalls and telemetry trackers, Ghost Agent brings that power back to your local machine. It serves as a locally hosted coding assistant that runs entirely on your own hardware, ensuring your code never leaves your network.

## 🧪 Collaborative Intelligence Disclaimer

> **Note:** This project is a demonstration of **Synthetic-Human Symbiosis**.

This codebase was developed through a synchronized workflow between myself and my own private, locally-hosted AI instance. 
* **The Goal:** To prove that high-level software engineering does not require reliance on cloud-based, closed-source "Big AI" providers.
* **The Result:** An open-source agent built *by* an open-source agent, for the community.

## ✨ Key Features

* **🔒 100% Local Inference:** Designed to run with local backends (Ollama, Llama.cpp), ensuring zero data leakage.
* **⚡ Low Latency:** Optimized for rapid completion generation on consumer hardware.
* **🚫 Anti-Telemetry:** No "usage data" sent to the cloud. Your keystrokes are yours.
* **🐍 Pure Python:** Lightweight codebase that is easy to audit and modify.

## 🛠️ Architecture

Ghost Agent acts as a bridge between your editor environment and a local Large Language Model (LLM).

1.  **Context Analysis:** Reads the current file and cursor position.
2.  **Local Request:** Formats a prompt optimized for code models (e.g., DeepSeek-Coder, CodeLlama).
3.  **Inference:** Queries the local API (e.g., `localhost:11434` for Ollama).
4.  **Injection:** Inserts the generated code snippet back into your workflow.

## 🚀 Getting Started

### Prerequisites

* **Python 3.10+**
* A local LLM runner (e.g., [Ollama](https://ollama.com/) or [LM Studio](https://lmstudio.ai/))

### Installation

1.  **Clone the repository**
    ```bash
    git clone [https://github.com/TRXAlpha/ghost-agent.git](https://github.com/TRXAlpha/ghost-agent.git)
    cd ghost-agent
    ```

2.  **Set up the Environment**
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    ```

3.  **Configure your Model**
    *Ensure your local LLM server is running.*
    ```bash
    # Example environment setup
    export LLM_API_URL="http://localhost:11434/api/generate"
    export MODEL_NAME="deepseek-coder:6.7b"
    ```

4.  **Run the Agent**
    ```bash
    python main.py
    ```

## 🗺️ Roadmap

- [x] v0.1: Basic Code Completion (CLI)
- [ ] v0.2: IDE Plugin Integration (VS Code Extension)
- [ ] v0.3: Context-Aware "Chat with Codebase"
- [ ] v1.0: Fine-tuning pipeline for personal coding styles

## 🤝 Contributing

We welcome anyone who wants to break free from the walled gardens.
1.  Fork the Project
2.  Create your Feature Branch
3.  Commit your Changes
4.  Push to the Branch
5.  Open a Pull Request

## 📜 License

Distributed under the MIT License. See `LICENSE` for more information.

---

<div align="center">
    
**Built with 💻 and 🤖 by [TRXAlpha](https://github.com/TRXAlpha)**

</div>
