from setuptools import setup, find_packages

setup(
    name="hermes-hxa-connect",
    version="1.0.0",
    description="Python HXA Connect SDK for Hermes Agent — bot-to-bot communication",
    author="HypergraphDev",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=["websockets>=12.0"],
    entry_points={
        "console_scripts": [
            "hxa-bot=hxa_connect.bot:run",
        ],
    },
)
