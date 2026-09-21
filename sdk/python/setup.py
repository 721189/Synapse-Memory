from setuptools import setup, find_packages

setup(
    name="synapse-memory",
    version="0.1.0",
    description="Sovereign, active RAG & cognitive long-term memory engine for AI agents",
    author="Synapse Cognitive Systems",
    author_email="singhshivam20009@gmail.com",
    url="https://github.com/721189/Synapse-Memory",
    packages=find_packages(include=["synapse_memory*"]),
    python_requires=">=3.10",
    install_requires=[
        "fastapi>=0.95.0",
        "pydantic>=2.0.0",
        "uvicorn>=0.20.0",
        "cryptography>=3.4.0",
        "requests>=2.25.0",
        "numpy>=1.20.0",
    ],
    extras_require={
        "local-models": ["sentence-transformers>=2.2.0"],
        "pgvector": ["psycopg2-binary>=2.9.0", "pgvector>=0.2.0"],
        "langchain": ["langchain-core>=0.1.0"],
        "llamaindex": ["llama-index-core>=0.10.0"],
        "all": [
            "sentence-transformers>=2.2.0",
            "psycopg2-binary>=2.9.0",
            "pgvector>=0.2.0",
            "langchain-core>=0.1.0",
            "llama-index-core>=0.10.0",
        ],
        "dev": [
            "flake8>=6.0.0",
            "mypy>=1.0.0",
            "bandit>=1.7.0",
            "pytest>=7.0.0",
            "types-cryptography",
            "types-requests",
        ],
    },
    entry_points={
        "console_scripts": [
            "synapse-memory=synapse_memory.cli.synapse_cli:main",
            "synapse=synapse_memory.cli.synapse_cli:main",
            "synapse-cli=synapse_memory.cli.synapse_cli:main",
        ]
    }
)
