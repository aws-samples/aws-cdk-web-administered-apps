# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_logs as logs,
)
from constructs import Construct
import aws_cdk as cdk

from aws_cdk import Aspects


class NetworkStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, params: map, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            params["app_name"].capitalize()
            + params["environment"].capitalize()
            + "Vpc",
            ip_addresses=ec2.IpAddresses.cidr(params["vpc_cidr_block"]),
            enable_dns_hostnames=True,
            enable_dns_support=True,
            nat_gateways=int(params["nat_gateway_count"]),
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Isolated", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
                ),
                ec2.SubnetConfiguration(
                    name="Private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                ),
                ec2.SubnetConfiguration(
                    name="Public", subnet_type=ec2.SubnetType.PUBLIC
                ),
            ],
        )

        # we add this tag so that the database stack can find the VPC
        Aspects.of(self.vpc).add(
            cdk.Tag(
                params["app_name"] + "-" + params["environment"] + "-network-stack",
                "vpc",
            )
        )

        vpc_fl_role = iam.Role(
            self,
            "VpcFlowLogsRole",
            assumed_by=iam.ServicePrincipal("vpc-flow-logs.amazonaws.com"),
        )

        self.vpc.add_flow_log(
            "VpcFlowLogs" + params["environment"],
            destination=ec2.FlowLogDestination.to_cloud_watch_logs(
                log_group=logs.LogGroup(
                    self, "VpcFlowLogsLogGroup" + params["environment"]
                ),
                iam_role=vpc_fl_role,
            ),
        )
        self.db_security_group = ec2.SecurityGroup(
            self,
            "RdsSecurityGroup",
            vpc=self.vpc,
            allow_all_outbound=False,
            description="RDS SG",
        )

        self.instance_security_group = ec2.SecurityGroup(
            self,
            "InstanceSecurityGroup",
            vpc=self.vpc,
            allow_all_outbound=False,
            description="Instance SG",
        )

        self.alb_security_group = ec2.SecurityGroup(
            self,
            "AlbSecurityGroup",
            vpc=self.vpc,
            allow_all_outbound=True,
            description="ALB SG",
        )

        self.db_security_group.add_ingress_rule(
            peer=self.instance_security_group,
            connection=ec2.Port.tcp(3306),
            description="Instances to Aurora",
        )

        self.instance_security_group.add_egress_rule(
            peer=self.db_security_group,
            connection=ec2.Port.tcp(3306),
            description="Instances to Aurora",
        )

        self.instance_security_group.add_ingress_rule(
            peer=self.alb_security_group,
            connection=ec2.Port.tcp(1880),
            description="ALB to Instances",
        )

        self.alb_security_group.add_ingress_rule(
            peer=ec2.Peer.prefix_list(params["cloudfront_prefix"]),
            connection=ec2.Port.tcp(443),
            description="CloudFront on port 443",
        )

        ssm_security_group = ec2.SecurityGroup(
            self,
            "SsmSecurityGroup",
            vpc=self.vpc,
            allow_all_outbound=True,
            description="SSM SG",
        )

        ssm_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(443),
            description="VPC CIDR on port 443",
        )

        ec2.InterfaceVpcEndpoint(
            self,
            "SecretsManagerVpcEndpoint",
            service=ec2.InterfaceVpcEndpointService(
                name="com.amazonaws." + self.region + ".secretsmanager"
            ),
            vpc=self.vpc,
            private_dns_enabled=True,
            open=True,
            security_groups=[ssm_security_group],
        )

        ec2.InterfaceVpcEndpoint(
            self,
            "SsmVpcEndpoint",
            service=ec2.InterfaceVpcEndpointService(
                name="com.amazonaws." + self.region + ".ssm"
            ),
            vpc=self.vpc,
            private_dns_enabled=True,
            open=True,
            security_groups=[ssm_security_group],
        )

        ec2.InterfaceVpcEndpoint(
            self,
            "Ec2MessagesVpcEndpoint",
            service=ec2.InterfaceVpcEndpointService(
                name="com.amazonaws." + self.region + ".ec2messages"
            ),
            vpc=self.vpc,
            private_dns_enabled=True,
            open=True,
            security_groups=[ssm_security_group],
        )

        ec2.InterfaceVpcEndpoint(
            self,
            "SsmMessagesVpcEndpoint",
            service=ec2.InterfaceVpcEndpointService(
                name="com.amazonaws." + self.region + ".ssmmessages"
            ),
            vpc=self.vpc,
            private_dns_enabled=True,
            open=True,
            security_groups=[ssm_security_group],
        )

        self.instance_security_group.add_ingress_rule(
            peer=ssm_security_group,
            connection=ec2.Port.tcp(443),
            description="Allow SSM to Instances",
        )

        self.instance_security_group.add_egress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(443),
            description="Allow any outbound 443",
        )

        self.instance_security_group.add_egress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(80),
            description="Allow any outbound 80",
        )

        self.export_value(
            name=self.stack_name + "RdsSecGroupId",
            exported_value=self.db_security_group.security_group_id,
        )
