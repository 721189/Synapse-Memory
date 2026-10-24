from setuptools import setup, find_packages

setup(
    name="synapse-memory-engine",
    version="1.2.0",
    description="Sovereign, active RAG & cognitive long-term memory engine for AI agents",
    author="Synapse Cognitive Systems",
    author_email="synapse-memory@users.noreply.github.com",
    url="https://github.com/721189/Synapse-Memory",
    license="All Rights Reserved",
    packages=find_packages(include=["synapse_memory*"]),
    python_requires=">=3.10",
    install_requires=[
        "fastapi>=0.115.0",
        "pydantic>=2.10.0",
        "uvicorn>=0.34.0",
        "cryptography>=42.0.0",
        "requests>=2.31.0",
        "numpy>=1.26.0",
    ],
    extras_require={
        "local-models": ["sentence-transformers>=5.0.0"],
        "pgvector": ["psycopg2-binary>=2.9.9.1", "pgvector>=0.4.0"],
        "langchain": ["langchain-core>=0.3.0"],
        "llamaindex": ["llama-index-core>=0.13.0"],
        "all": [
            "sentence-transformers>=5.0.0",
            "psycopg2-binary>=2.9.9.1",
            "pgvector>=0.4.0",
            "langchain-core>=0.3.0",
            "llama-index-core>=0.13.0",
        ],
        "dev": [
            "flake8>=7.0.0",
            "mypy>=1.13.0",
            "bandit>=1.8.0",
            "pytest>=8.0.0",
            "types-cryptography>=42.0.0",
            "types-requests>=2.32.0",
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
