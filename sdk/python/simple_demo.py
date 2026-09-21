#!/usr/bin/env python3
"""
Simple Demo Script representing Month 1 GTM Plan Deliverable.
Demonstrates:
  - SQLite persistence
  - Semantic Deduplication
  - Knapsack Dynamic Programming
  - Temporal forgetting calculations
"""
import os
import sys

# Ensure local synapse_memory library imports successfully
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from synapse_memory.main import run_production_demo

if __name__ == "__main__":
    run_production_demo()
