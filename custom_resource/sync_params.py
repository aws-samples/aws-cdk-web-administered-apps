# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import print_function
from crhelper import CfnResource
import logging
import json
import boto3

logger = logging.getLogger(__name__)
# Initialise the helper, all inputs are optional, this example shows the defaults
helper = CfnResource(
    json_logging=False,
    log_level="DEBUG",
    boto_level="CRITICAL",
    sleep_on_delete=120,
    ssl_verify=None,
)
ssm_client = boto3.client('ssm', region_name="us-east-1")


def update_parameter(event):
    # try the update
    try:
        response = ssm_client.put_parameter(
            Name=event["ResourceProperties"].get("alb_parameter_name"),
            Description='ALB DNS name',
            Value=event["ResourceProperties"].get("alb_hostname"),
            Type='String',
            Overwrite=True,
            Tier='Standard',
        )
        logger.info("update_parameter returned " + json.dumps(response))
        response2 = ssm_client.put_parameter(
            Name=event["ResourceProperties"].get("cf_parameter_name"),
            Description='CloudFront secret',
            Value=event["ResourceProperties"].get("cf_secret_value"),
            Type='String',
            Overwrite=True,
            Tier='Standard',
        )
        logger.info("update_parameter returned " + json.dumps(response2))
    except Exception as e:
        logger.exception(e)
        raise ValueError(
            "An error occurred when attempting to update the parameter. See the CloudWatch logs for details"
        )


@helper.create
def create(event, context):
    logger.info("Got Create")
    logger.info(json.dumps(event))
    update_parameter(event)
    return None


@helper.update
def update(event, context):
    logger.info("Got update event")
    logger.info(json.dumps(event))
    # compare the custom emailer ARN and if it's changed, update the user pool
    if event["ResourceProperties"].get("alb_hostname", None) != event[
        "OldResourceProperties"
    ].get("alb_hostname"):
        logger.info("Alb details have changed, so doing an update")
        update_parameter(event)
    elif event["ResourceProperties"].get("cf_secret_value", None) != event[
        "OldResourceProperties"
    ].get("cf_secret_value"):
        logger.info("CF details have changed, so doing an update")
        update_parameter(event)
    else:
        logger.info("data hasn't changed, so not doing an update")


# Delete never returns anything.
# Should not fail if the underlying resources are already deleted.
@helper.delete
def delete(event, context):
    logger.info("Got Delete")
    # I can't think of a good way to enable this without it causing more issues than it solves
    # ssm_client.delete_parameter(
    #     Name=event["ResourceProperties"].get("cf_parameter_name")
    # )
    # ssm_client.delete_parameter(
    #     Name=event["ResourceProperties"].get("alb_parameter_name")
    # )


# @helper.poll_create
# def poll_create(event, context):
#     logger.info("Got create poll")
#     # Return a resource id or True to indicate that creation is complete.
#     # If True is returned an id will be generated
#     return True


def handler(event, context):
    # logger.info(json.dumps(event))
    helper(event, context)
