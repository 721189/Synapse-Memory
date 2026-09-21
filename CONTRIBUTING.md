# Contributing to Synapse Memory

We welcome contributions to Synapse Memory! To maintain engineering rigor, please follow these guidelines.

## Development Workflow

1. **Fork and Clone**: Fork the repository and clone it to your local machine.
2. **Setup Environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt -r requirements-dev.txt
   pip install -e .
   ```
3. **Branching**: Use descriptive branch names (e.g., `feature/add-new-provider` or `fix/sqlite-locking-issue`).
4. **Code Standards**:
   - Follow PEP 8 guidelines.
   - Run `flake8` to check for style issues.
   - Run `mypy` to verify type annotations.
5. **Testing**:
   - Write unit tests for new features.
   - Run `pytest` to execute the full test suite. Ensure all tests pass before submitting a PR.
6. **Documentation**: Update documentation if you change an API or add a new feature.

## Pull Request Process

1. Ensure your code passes all tests and linting.
2. Submit a Pull Request with a clear description of the changes.
3. Link any relevant issues.
4. Your PR will be reviewed and merged upon approval.

## Code of Conduct

Please be respectful and professional in all interactions.
