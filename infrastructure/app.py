#!/usr/bin/env python3
import os
import aws_cdk as cdk
from infrastructure.infrastructure_stack import InfrastructureStack
from infrastructure.frontend_stack import FrontendStack

app = cdk.App()

# Get configuration from context or environment
project_name = app.node.try_get_context("project_name") or os.getenv("PROJECT_NAME", "mvip")
environment = app.node.try_get_context("environment") or os.getenv("ENVIRONMENT", "prod")

# AWS environment (uses current AWS CLI configuration by default)
env = cdk.Environment(
    account=os.getenv('CDK_DEFAULT_ACCOUNT'),
    region=os.getenv('CDK_DEFAULT_REGION', 'us-east-1')
)

# Deploy infrastructure stack
infra_stack = InfrastructureStack(
    app,
    "InfrastructureStack",
    project_name=project_name,
    env=env,
    description="Smart Video Search System - Main Infrastructure"
)

# Deploy frontend stack
frontend_stack = FrontendStack(
    app,
    "FrontendStack",
    project_name=project_name,
    api_endpoint=infra_stack.api_endpoint,
    env=env,
    description="Smart Video Search System - Frontend"
)

# Add dependency - frontend needs infrastructure to exist first
frontend_stack.add_dependency(infra_stack)

app.synth()

