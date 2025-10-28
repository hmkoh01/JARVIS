# 🦜🕸️ JARVIS - A Personalized Multi-Agent AI System

JARVIS is a sophisticated multi-agent system built on the LangGraph framework. It goes beyond a simple chatbot, providing a suite of AI agents that collaborate to perform a variety of intelligent tasks based on your local data. With a user-friendly startup script, JARVIS makes complex setup processes effortless, allowing you to harness the power of personalized AI assistants.

## 🌟 Core Features

- **Multi-Agent Architecture**: Powered by LangGraph, JARVIS orchestrates multiple specialized AI agents (Chatbot, Coding, Dashboard, Recommendation) that work together to handle complex tasks.
- **Personalized RAG**: The system builds a Retrieval-Augmented Generation (RAG) pipeline based on your local files. You select the folders, and JARVIS uses them as a knowledge base to provide contextually accurate answers.
- **Automated Environment Setup**: A single `start.py` script checks for dependencies (Docker, Python packages), configures the environment, and launches all necessary services.
- **User-Friendly Interface**: Features a desktop floating chat widget for easy access and a GUI-based setup process for user surveys and folder selection.
- **Data-Driven Intelligence**: Automatically collects and indexes data from your selected files, enabling agents to provide personalized insights, visualizations, and recommendations.

## 🛠️ System Architecture

JARVIS is composed of a FastAPI backend, a user-facing frontend, and a suite of intelligent agents.

### 1. Backend (`/backend`)
- **API Server (`main.py`)**: A FastAPI server that manages agent lifecycle and handles all API requests.
- **Agent Core (`/agents`)**: Contains the logic for each specialized agent:
    - `ChatbotAgent`: Interacts with the user via a RAG-based conversational model.
    - `CodingAgent`: Assists with code generation, debugging, and other development tasks.
    - `DashboardAgent`: Analyzes data and generates insightful visualizations.
    - `RecommendationAgent`: Provides personalized recommendations based on user data.
- **Database (`/database`)**: Manages data collection from local files, user profiles, and metadata storage using SQLite and a Qdrant vector database.

### 2. Frontend (`/frontend`)
- **Floating Chat App (`front.py`)**: A desktop widget that serves as the primary user interface for interacting with JARVIS.
- **Setup Dialogs (`survey_dialog.py`, `folder_selector.py`)**: GUI components that guide the user through the initial setup process.

### 3. Launcher (`start.py`)
This script is the main entry point for the entire system. It performs the following steps in order:
1.  Checks if Docker and required Python packages are installed.
2.  Verifies the existence of the `.env` configuration file.
3.  Starts the Qdrant vector database using Docker.
4.  Initializes the SQLite database.
5.  Launches the backend FastAPI server.
6.  Prompts the user with a survey and a folder selection dialog.
7.  Initiates the initial data collection and indexing process from the selected folders.
8.  Starts the desktop floating chat application.

## 🚀 Getting Started

1.  **Prerequisites**:
    -   Python 3.10+
    -   Docker Desktop (running)

2.  **Installation**:
    ```bash
    # Clone the repository
    git clone https://github.com/your-repo/JARVIS.git
    cd JARVIS

    # Install dependencies
    pip install -r requirements.txt
    ```

3.  **Configuration**:
    -   You may need to create a `.env` file based on the startup script's guidance.
    -   Add your `GEMINI_API_KEY` to the `.env` file if required.

4.  **Running the System**:
    ```bash
    python start.py
    ```
    The script will guide you through the rest of the setup process. Enjoy your personalized AI assistant!
