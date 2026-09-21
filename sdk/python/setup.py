from setuptools import setup, find_packages

setup(
    name="synapse-memory",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "fastapi>=0.95.0",
        "pydantic>=2.0.0",
        "uvicorn>=0.20.0",
        "cryptography>=3.4.0"
    ],
    extras_require={
        "langchain": ["langchain-core>=0.1.0"],
        "llamaindex": ["llama-index-core>=0.10.0"]
    },
    entry_points={
        "console_scripts": [
            "synapse-cli=synapse_memory.cli.synapse_cli:main"
        ]
    }
)
