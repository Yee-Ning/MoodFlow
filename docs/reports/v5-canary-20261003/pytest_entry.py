"""Remove the R2 wrapper's fake key so dotenv precedence tests own their key."""
import os
import runpy
import sys

os.environ.pop("TYPESAFE_API_KEY", None)
sys.argv = ["pytest", *sys.argv[1:]]
runpy.run_module("pytest", run_name="__main__")
