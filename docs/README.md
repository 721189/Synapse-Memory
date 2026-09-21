# Documentation Build Guide

To build the documentation, you need to have `sphinx` and `sphinx-rtd-theme` installed in your Python environment:

```bash
pip install sphinx sphinx-rtd-theme
```

Then, run the following command from the project root:

```bash
sphinx-build -b html docs/ docs/_build
```
