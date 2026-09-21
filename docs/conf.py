import os
import sys
sys.path.insert(0, os.path.abspath('../sdk/python'))

project = 'Synapse Memory'
copyright = '2026, AI Assistant'
author = 'AI Assistant'
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
]
templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']
html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']
