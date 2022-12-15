# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from aws_cdk import Stack
from constructs import Construct
import aws_cdk as cdk
from cdk_nag import NagSuppressions, NagPackSuppression

from aws_cdk import aws_ec2 as ec2, aws_rds as rds, aws_ssm as ssm
import re


class DatabaseStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, params: map, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.security_group = None
        self.db = None

        if "db_config" in params and params["db_config"] == "":
            return

        # use tags to get the VPC because exports are only tokens at synth time
        vpc = ec2.Vpc.from_lookup(
            self,
            "Vpc",
            is_default=False,
            tags={self.stack_name.replace("database", "network"): "vpc"},
        )

        database_sg = ec2.SecurityGroup.from_security_group_id(
            self,
            "DbSg",
            security_group_id=cdk.Fn.import_value(
                self.stack_name.replace("database", "network") + "RdsSecGroupId"
            ),
        )

        rds_subnet = rds.SubnetGroup(
            self,
            "RdsSubnetGroup",
            description="Subnet group for Appname RDS instance",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
        )
        db_engine = rds.DatabaseInstanceEngine.mysql(
            version=rds.MysqlEngineVersion.of(
                mysql_major_version=params["db_major_version"],
                mysql_full_version=params["db_full_version"],
            )
        )
        db_logging = ["audit", "error", "general", "slowquery"]
        if params["db_engine"] == "postgres":
            db_engine = rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.of(
                    postgres_major_version=params["db_major_version"],
                    postgres_full_version=params["db_full_version"],
                )
            )
            db_logging = []

        if "db_config" in params and params["db_config"] == "instance":
            if (
                "db_snapshot_id" in params
                and re.search('snapshot', params["db_snapshot_id"]) != None
            ):
                self.db = rds.DatabaseInstanceFromSnapshot(
                    self,
                    params["app_name"].capitalize()
                    + params["environment"].capitalize()
                    + "Database",
                    cloudwatch_logs_exports=db_logging,
                    deletion_protection=params["prevent_deletion"],
                    engine=db_engine,
                    multi_az=True,
                    storage_encrypted=True,
                    snapshot_identifier=params["db_snapshot_id"],
                    instance_type=ec2.InstanceType(
                        instance_type_identifier=params["db_instance_type"]
                    ),
                    vpc_subnets=ec2.SubnetSelection(
                        subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    ),
                    vpc=vpc,
                    security_groups=[database_sg],
                    subnet_group=rds_subnet,
                    backup_retention=cdk.Duration.days(35),
                )
            else:
                self.db = rds.DatabaseInstance(
                    self,
                    params["app_name"].capitalize()
                    + params["environment"].capitalize()
                    + "Database",
                    cloudwatch_logs_exports=db_logging,
                    deletion_protection=params["prevent_deletion"],
                    engine=db_engine,
                    database_name=params["app_name"],
                    multi_az=True,
                    storage_encrypted=True,
                    instance_type=ec2.InstanceType(
                        instance_type_identifier=params["db_instance_type"]
                    ),
                    vpc_subnets=ec2.SubnetSelection(
                        subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    ),
                    vpc=vpc,
                    security_groups=[database_sg],
                    subnet_group=rds_subnet,
                    backup_retention=cdk.Duration.days(35),
                )
        elif "db_config" in params and params["db_config"] == "cluster":
            if (
                "db_snapshot_id" in params
                and re.search('snapshot', params["db_snapshot_id"]) != None
            ):
                self.db = rds.DatabaseClusterFromSnapshot(
                    self,
                    params["app_name"].capitalize()
                    + params["environment"].capitalize()
                    + "Database",
                    backtrack_window=cdk.Duration.hours(72),
                    deletion_protection=params["prevent_deletion"],
                    cloudwatch_logs_exports=db_logging,
                    engine=rds.DatabaseClusterEngine.AURORA_MYSQL,
                    parameter_group=rds.ParameterGroup(
                        self,
                        "DatabaseParamGroup",
                        engine=rds.DatabaseClusterEngine.AURORA_MYSQL,
                        parameters={
                            "aurora_parallel_query": "ON",
                            "aurora_disable_hash_join": "OFF",
                        },
                    ),
                    default_database_name=params["app_name"],
                    snapshot_identifier=params["db_snapshot_id"],
                    instances=params["db_cluster_size"][params["environment"]],
                    storage_encrypted=True,
                    instance_props=rds.InstanceProps(
                        instance_type=ec2.InstanceType(
                            instance_type_identifier=params["db_instance_type"]
                        ),
                        vpc_subnets=ec2.SubnetSelection(
                            subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                        ),
                        vpc=vpc,
                        security_groups=[database_sg],
                    ),
                    subnet_group=rds_subnet,
                    backup=rds.BackupProps(retention=cdk.Duration.days(35)),
                )
            else:
                self.db = rds.DatabaseCluster(
                    self,
                    "AppnameDatabaseCluster",
                    backtrack_window=cdk.Duration.hours(72),
                    cloudwatch_logs_exports=db_logging,
                    deletion_protection=params["prevent_deletion"],
                    engine=rds.DatabaseClusterEngine.AURORA_MYSQL,
                    parameter_group=rds.ParameterGroup(
                        self,
                        params["app_name"].capitalize()
                        + params["environment"].capitalize()
                        + "Database",
                        engine=rds.DatabaseClusterEngine.AURORA_MYSQL,
                        parameters={
                            "aurora_parallel_query": "ON",
                            "aurora_disable_hash_join": "OFF",
                        },
                    ),
                    default_database_name=params["app_name"],
                    instances=params["db_cluster_size"][params["environment"]],
                    storage_encrypted=True,
                    instance_props=rds.InstanceProps(
                        instance_type=ec2.InstanceType(
                            instance_type_identifier=params["db_instance_type"]
                        ),
                        vpc_subnets=ec2.SubnetSelection(
                            subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                        ),
                        vpc=vpc,
                        security_groups=[database_sg],
                    ),
                    subnet_group=rds_subnet,
                    backup=rds.BackupProps(retention=cdk.Duration.days(35)),
                )

        if self.db:
            ssm.StringParameter(
                self,
                "DbSecret",
                string_value=self.db.secret.secret_name,
                parameter_name="/"
                + params["app_name"]
                + "/"
                + params["environment"]
                + "/DatabaseSecret",
            )

        if params["db_config"] != "delete" and params["db_config"] != "none":
            self.db.apply_removal_policy(cdk.RemovalPolicy.SNAPSHOT)

            NagSuppressions.add_resource_suppressions(
                self.db,
                suppressions=[
                    NagPackSuppression(
                        id="AwsSolutions-RDS11",
                        reason="Want default port because not always possible to reconfigure the app to use non-standard port",
                    ),
                ],
            )
