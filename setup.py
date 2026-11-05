# -*- coding: utf-8 -*-
from codecs import open
from os import path

from setuptools import find_packages, setup

here = path.abspath(path.dirname(__file__))

with open(path.join(here, "README.md"), encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="ckanext-mcp_assistant",
    version="2.0.0",
    description="Natural-language assistant for CKAN portals: an LLM answers questions "
                "about the portal through a CKAN MCP server. Originally developed for the "
                "Leibniz Data Manager (LDM).",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/SDM-TIB/ckanext-mcp_assistant",
    author="Ariam Rivas",
    author_email="ariam.rivas@tib.eu",
    license="AGPL",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Framework :: CKAN",
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    keywords="CKAN MCP LLM AI assistant chatbot natural-language search",
    python_requires=">=3.8",
    packages=find_packages(exclude=["contrib", "docs", "tests*"]),
    namespace_packages=["ckanext"],
    install_requires=[
        # requests is already a CKAN requirement; nothing else is needed.
    ],
    include_package_data=True,
    package_data={},
    data_files=[],
    entry_points="""
        [ckan.plugins]
        mcp_assistant=ckanext.mcp_assistant.plugin:McpAssistantPlugin

        [babel.extractors]
        ckan = ckan.lib.extract:extract_ckan
    """,
    message_extractors={
        "ckanext": [
            ("**.py", "python", None),
            ("**.js", "javascript", None),
            ("**/templates/**.html", "ckan", None),
        ],
    },
)
