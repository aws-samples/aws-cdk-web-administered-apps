#!/usr/bin/env python3

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import configparser
import re
import os
import json
import boto3
import aws_cdk as cdk
from aws_cdk import Aspects

from app_stacks.network_stack import NetworkStack
from app_stacks.database_stack import DatabaseStack
from app_stacks.compute_stack import ComputeStack
from app_stacks.cdn_stack import CdnStack

from cdk_nag import AwsSolutionsChecks, NagSuppressions, NagPackSuppression

session = boto3.session.Session()

config = configparser.ConfigParser()
config.read("parameters.properties")

app = cdk.App()

params = {}
params["environment"] = app.node.try_get_context("env") or config["default"]["env"]
params["app_name"] = app.node.try_get_context("app") or config["default"]["app"]
env_config = params["app_name"] + "-" + params["environment"]

params["aws_region"] = (
    config[env_config]["awsRegion"] or os.environ["CDK_DEFAULT_REGION"]
)
params["aws_account"] = (
    config[env_config]["awsAccount"] or os.environ["CDK_DEFAULT_ACCOUNT"]
)

# params["aws_region"] = config[env_config]["awsRegion"]
# params["aws_account"] = config[env_config]["awsAccount"]
params["hosted_zone"] = config[env_config]["hostedZone"]
# derive the hosted_zone_id from the hosted_zone
hosted_zone_json = os.popen(
    'aws route53 list-hosted-zones-by-name --dns-name ' + params["hosted_zone"]
).read()
params["hosted_zone_id"] = str(
    json.loads(hosted_zone_json)['HostedZones'][0]['Id']
).replace('/hostedzone/', '')
# get the cloudfront_prefix for the current region
cloudfront_prefix_json = os.popen(
    'aws ec2 describe-managed-prefix-lists --filter "Name"="prefix-list-name","Values"="com.amazonaws.global.cloudfront.origin-facing"'
).read()
params["cloudfront_prefix"] = json.loads(cloudfront_prefix_json)['PrefixLists'][0][
    'PrefixListId'
]
params["vpc_cidr_block"] = config[env_config]["vpcCidrBlock"]
params["nat_gateway_count"] = config[env_config]["natGatewayCount"]
params["subdomain"] = config[env_config]["subdomain"]
params["db_config"] = config[env_config]["dbConfig"]
params["db_snapshot_id"] = config[env_config]["dbSnapshot"]
params["db_engine"] = config[env_config]["dbEngine"]
params["db_major_version"] = config[env_config]["dbMajorVersion"]
params["db_full_version"] = config[env_config]["dbFullVersion"]
params["db_instance_type"] = config[env_config]["dbInstanceType"]
params["db_cluster_size"] = config[env_config]["dbClusterSize"]
params["db_secret_name"] = config[env_config]["dbSecretName"]
params["prevent_deletion"] = config[env_config]["preventDeletion"] == "yes"
params["ami_parameter"] = config[env_config]["amiParameter"]
params["admin_ips"] = config[env_config]["adminIps"].split(",")
params["allowed_ips"] = config[env_config]["allowedIps"].split(",")
params["efs_mount_dir"] = config[env_config]["efsMountDir"]
params["efs_provisioned_throughput_mb"] = config[env_config][
    "efsProvisionedThroughputMb"
]
params["target_port"] = config[env_config]["targetPort"]
params["admin_user_data"] = json.loads(config[env_config]["adminUserData"])
params["fleet_user_data"] = json.loads(config[env_config]["fleetUserData"])
params["admin_user_data_script"] = config[env_config]["adminUserDataScript"]
params["fleet_user_data_script"] = config[env_config]["fleetUserDataScript"]
params["managed_waf_rules"] = json.loads(config[env_config]["managedWafRules"])
params["uncached_paths"] = json.loads(config[env_config]["uncachedPaths"])
params["forwarded_cookies"] = json.loads(config[env_config]["forwardedCookies"])
params["min_max_admin_instances"] = json.loads(
    config[env_config]["minMaxAdminInstances"]
)
params["min_max_fleet_instances"] = json.loads(
    config[env_config]["minMaxFleetInstances"]
)
params["admin_instance_type"] = config[env_config]["adminInstanceType"]
params["fleet_instance_type"] = config[env_config]["fleetInstanceType"]
params["admin_build_time"] = config[env_config]["adminBuildTime"]
params["fleet_build_time"] = config[env_config]["fleetBuildTime"]
deploy_environment = cdk.Environment(
    region=params["aws_region"], account=params["aws_account"]
)

global_environment = cdk.Environment(region="us-east-1", account=params["aws_account"])

# derive site_url
dns_pattern = re.compile(r'^[a-zA-Z]+[a-zA-Z\d-]{,62}')
if "subdomain" in params and re.search(dns_pattern, params["subdomain"]) != None:
    params["site_hostname"] = params["subdomain"] + "." + params["hosted_zone"]
else:
    params["site_hostname"] = (
        params["app_name"] + "-" + params["environment"] + "." + params["hosted_zone"]
    )

# The param we will write to the parameter store in us-east-1
params["alb_hostname_param"] = (
    "/" + params["app_name"] + "/" + params["environment"] + "/" + "alb-hostname"
)
params["cloudfront_secret_param"] = (
    "/" + params["app_name"] + "/" + params["environment"] + "/" + "cloudfront-secret"
)

# use standardised stack names so we can use them to resolve cross-stack cloudformation imports
network_stack = NetworkStack(
    app,
    params["app_name"] + "-" + params["environment"] + "-network-stack",
    params=params,
    env=deploy_environment,
)

db_secret_name = params["db_secret_name"] or ""

if (
    params["db_config"] == "instance"
    or params["db_config"] == "cluster"
    or params["db_config"] == "none"
):
    database_stack = DatabaseStack(
        app,
        params["app_name"] + "-" + params["environment"] + "-database-stack",
        params=params,
        env=deploy_environment,
    )
    Aspects.of(database_stack).add(AwsSolutionsChecks())
    if params["db_config"] != "delete" and params["db_config"] != "none":
        db_secret_name = database_stack.db.secret.secret_name

compute_stack = ComputeStack(
    app,
    params["app_name"] + "-" + params["environment"] + "-compute-stack",
    vpc=network_stack.vpc,
    instance_sg=network_stack.instance_security_group,
    alb_sg=network_stack.alb_security_group,
    db_secret_name=db_secret_name,
    params=params,
    env=deploy_environment,
)

if db_secret_name != "":
    compute_stack.add_dependency(database_stack)

# try fetching params["alb_hostname_param"] from us-east-1 and if it's there we can synth this stack
try:
    ssm_client = session.client(
        service_name='ssm',
        region_name='us-east-1',
    )
    alb_hostname_param = ssm_client.get_parameter(Name=params["alb_hostname_param"])
    if 'Parameter' in alb_hostname_param:
        cdn_stack = CdnStack(
            app,
            params["app_name"] + "-" + params["environment"] + "-cdn-stack",
            params=params,
            env=global_environment,
        ).add_dependency(compute_stack)
        # can't add the solutions check unless compute stack is present
        # as unless it is cdn_stack will == None
        if cdn_stack != None:
            Aspects.of(cdn_stack).add(AwsSolutionsChecks())
except Exception as e:
    if str(e).find("ParameterNotFound") != -1:
        print(
            "Not synthing CDN stack as the parameter {} was not found in us-east-1. It will get created once you have deployed the compute stack.".format(
                params["alb_hostname_param"]
            )
        )
    else:
        print(e)

Aspects.of(network_stack).add(AwsSolutionsChecks())
Aspects.of(compute_stack).add(AwsSolutionsChecks())
# have to suppress some nags at stack level because the resources that
# are flagged are generated inside L2 constructs
NagSuppressions.add_stack_suppressions(
    compute_stack,
    suppressions=[
        NagPackSuppression(
            id='AwsSolutions-L1', reason='Lambda created by embedded library'
        ),
        NagPackSuppression(id='AwsSolutions-IAM4', reason='CDK-generated policy'),
        NagPackSuppression(id='AwsSolutions-IAM5', reason='CDK-generated IAM entity'),
    ],
)
NagSuppressions.add_stack_suppressions(
    database_stack,
    suppressions=[
        NagPackSuppression(id='AwsSolutions-SMG4', reason='CDK-generated secret'),
    ],
)

Aspects.of(app).add(cdk.Tag("CreatedBy", "guymor@amazon.com"))
# add more tags here

app.synth()
