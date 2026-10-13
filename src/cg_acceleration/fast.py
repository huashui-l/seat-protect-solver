"""Experimental hybrid pricing with certified singleton enumeration."""
from . import __main__ as entry
from .singleton import SingletonPricer


if __name__ == "__main__":
    entry.HybridPricer = SingletonPricer
    entry.main()
