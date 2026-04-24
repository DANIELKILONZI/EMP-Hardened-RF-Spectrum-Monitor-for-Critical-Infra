"""Setup configuration for EMP-Hardened RF Spectrum Monitor."""

from setuptools import find_packages, setup

with open("README.md", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="emp-rf-spectrum-monitor",
    version="1.0.0",
    author="RF Spectrum Monitor Team",
    description=(
        "EMP-Hardened RF Spectrum Monitor for Critical Infrastructure – "
        "detects rogue drones/jammers across 100 MHz – 6 GHz."
    ),
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(where=".", exclude=["tests*"]),
    python_requires=">=3.9",
    install_requires=[
        "numpy>=1.24.0",
        "pyyaml>=6.0",
        "pysnmp>=6.1.0",
        "pyserial>=3.5",
    ],
    extras_require={
        "rtlsdr": ["pyrtlsdr>=0.3.0"],
        "usrp": [],  # uhd is installed via system package manager
        "viz": ["matplotlib>=3.7.0"],
        "dashboard": ["flask>=3.0.0"],
        "ml": ["scikit-learn>=1.5.0", "joblib>=1.3.0"],
        "geo": ["scipy>=1.11.0"],
        "dev": [
            "pytest>=7.4.0",
            "pytest-cov>=4.1.0",
            "flask>=3.0.0",
            "scikit-learn>=1.5.0",
        ],
        "all": [
            "flask>=3.0.0",
            "matplotlib>=3.7.0",
            "scikit-learn>=1.5.0",
            "joblib>=1.3.0",
            "scipy>=1.11.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "rf-monitor=src.monitor:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Physics",
        "Topic :: Security",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Operating System :: POSIX :: Linux",
    ],
)
