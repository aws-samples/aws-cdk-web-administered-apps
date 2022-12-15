# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_ssm as ssm,
    aws_iam as iam,
    aws_autoscaling as autoscaling,
    aws_sns as sns,
    aws_kms as kms,
    aws_elasticloadbalancingv2 as elbv2,
    aws_efs as efs,
    aws_secretsmanager as secretsmanager,
    aws_lambda as aws_lambda,
    aws_certificatemanager as acm,
    aws_route53 as route53,
    aws_logs as logs,
)
import aws_cdk as cdk
import re
from aws_cdk import CustomResource
import aws_cdk.custom_resources as cr
from os import path
from constructs import Construct
from cdk_nag import NagSuppressions, NagPackSuppression


class ComputeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.Vpc,
        instance_sg: ec2.SecurityGroup,
        alb_sg: ec2.SecurityGroup,
        db_secret_name: str,
        params: map,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        efs_fs = efs.FileSystem(
            self,
            "EfsFileSystem",
            vpc=vpc,
            enable_automatic_backups=True,
            encrypted=True,
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            throughput_mode=efs.ThroughputMode.ELASTIC,
            lifecycle_policy=efs.LifecyclePolicy.AFTER_14_DAYS,  # files are not transitioned to infrequent access (IA) storage by default
            out_of_infrequent_access_policy=efs.OutOfInfrequentAccessPolicy.AFTER_1_ACCESS,
            file_system_name=params["app_name"]
            + "-"
            + params["environment"]
            + "-filesystem",
            security_group=instance_sg,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        if params["efs_provisioned_throughput_mb"] != '':
            efs_fs = efs.FileSystem(
                self,
                "EfsFileSystem",
                vpc=vpc,
                enable_automatic_backups=True,
                encrypted=True,
                performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
                throughput_mode=efs.ThroughputMode.PROVISIONED,
                provisioned_throughput_per_second=cdk.Size.mebibytes(
                    int(params["efs_provisioned_throughput_mb"])
                ),
                lifecycle_policy=efs.LifecyclePolicy.AFTER_14_DAYS,  # files are not transitioned to infrequent access (IA) storage by default
                out_of_infrequent_access_policy=efs.OutOfInfrequentAccessPolicy.AFTER_1_ACCESS,
                file_system_name=params["app_name"]
                + "-"
                + params["environment"]
                + "-filesystem",
                security_group=instance_sg,
                removal_policy=cdk.RemovalPolicy.RETAIN,
                vpc_subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                ),
            )

        efs_fs.connections.allow_default_port_internally()

        db_secret_command = ""
        db_secret = None

        if db_secret_name != "":
            db_secret = secretsmanager.Secret.from_secret_name_v2(
                self, "DbSecret", secret_name=db_secret_name
            )
            db_secret_command = (
                "aws secretsmanager get-secret-value --secret-id "
                + db_secret.secret_arn
                + " --region "
                + self.region
                + " --query SecretString --output text"
            )

        admin_instance_role = iam.Role(
            self, "InstanceRole", assumed_by=iam.ServicePrincipal("ec2.amazonaws.com")
        )

        admin_instance_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonSSMManagedInstanceCore"
            )
        )
        admin_instance_role.add_managed_policy(
            iam.ManagedPolicy(
                self,
                "EfsRwPolicy",
                statements=[
                    iam.PolicyStatement(
                        actions=[
                            "elasticfilesystem:ClientMount",
                            "elasticfilesystem:ClientWrite",
                            "elasticfilesystem:ClientRootAccess",
                        ],
                        effect=iam.Effect.ALLOW,
                        resources=[efs_fs.file_system_arn],
                        conditions={
                            "Bool": {"elasticfilesystem:AccessedViaMountTarget": "true"}
                        },
                    )
                ],
            )
        )
        if db_secret != None:
            secrets_policy = iam.ManagedPolicy(
                self,
                "SecretsPolicy",
                statements=[
                    iam.PolicyStatement(
                        actions=[
                            "secretsmanager:GetResourcePolicy",
                            "secretsmanager:GetSecretValue",
                            "secretsmanager:DescribeSecret",
                            "secretsmanager:ListSecretVersionIds",
                        ],
                        effect=iam.Effect.ALLOW,
                        resources=[
                            "arn:aws:secretsmanager:"
                            + self.region
                            + ":"
                            + self.account
                            + ":secret:"
                            + params["app_name"].capitalize()
                            + params["environment"].capitalize()
                            + "DatabaseSecret*"
                        ],
                    ),
                    iam.PolicyStatement(
                        actions=[
                            "secretsmanager:ListSecrets",
                        ],
                        effect=iam.Effect.ALLOW,
                        resources=["*"],
                    ),
                ],
            )
            admin_instance_role.add_managed_policy(secrets_policy)

            NagSuppressions.add_resource_suppressions(
                secrets_policy,
                suppressions=[
                    NagPackSuppression(
                        id="AwsSolutions-IAM5",
                        reason="* is needed in this case, see https://docs.aws.amazon.com/mediaconnect/latest/ug/iam-policy-examples-asm-secrets.html",
                    ),
                ],
            )

        scaling_events_topic = sns.Topic(
            self,
            "SnsScalingEvents",
            master_key=kms.Key(self, "SnsKey", enable_key_rotation=True),
        )
        notification_configuration = autoscaling.NotificationConfiguration(
            topic=scaling_events_topic, scaling_events=autoscaling.ScalingEvents.ALL
        )

        ami_id = ssm.StringParameter.value_from_lookup(
            self, parameter_name=params["ami_parameter"]
        )

        app_ami = ec2.MachineImage.generic_linux(
            ami_map={self.region: ami_id},
        )

        admin_user_data = ec2.UserData.for_linux()

        def interpolate_vars(string):
            return string.format(
                efs_fs_id=efs_fs.file_system_id,
                efs_mount_dir=params["efs_mount_dir"],
                site_hostname=params["site_hostname"],
                db_secret_command=db_secret_command,
            )

        admin_user_data.add_commands(
            *list(map(interpolate_vars, params["admin_user_data"]))
        )

        if params["admin_user_data_script"] and path.exists(
            "./userdata/" + params["admin_user_data_script"]
        ):
            userdata_file = open(
                "./userdata/" + params["admin_user_data_script"], "r"
            ).read()
            admin_user_data.add_commands(interpolate_vars(str(userdata_file)))

        admin_asg = autoscaling.AutoScalingGroup(
            self,
            "AdminASG",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            health_check=autoscaling.HealthCheck.elb(
                grace=cdk.Duration.minutes(int(params["admin_build_time"]))
            ),
            launch_template=ec2.LaunchTemplate(
                self,
                params["app_name"].capitalize()
                + params["environment"].capitalize()
                + "AdminLaunchTemplate",
                role=admin_instance_role,
                user_data=admin_user_data,
                ebs_optimized=True,
                machine_image=app_ami,
                security_group=instance_sg,
                instance_type=ec2.InstanceType(
                    instance_type_identifier=params["admin_instance_type"]
                ),
            ),
            min_capacity=params["min_max_admin_instances"][0],
            max_capacity=params["min_max_admin_instances"][1],
            notifications=[notification_configuration],
            update_policy=autoscaling.UpdatePolicy.replacing_update(),
        )

        write_targets = elbv2.ApplicationTargetGroup(
            self,
            "WriteTarget",
            targets=[admin_asg],
            protocol_version=elbv2.ApplicationProtocolVersion.HTTP1,
            protocol=elbv2.ApplicationProtocol.HTTP,
            port=int(params["target_port"]),
            health_check=elbv2.HealthCheck(
                enabled=True,
                unhealthy_threshold_count=5,
                healthy_threshold_count=2,
                timeout=cdk.Duration.seconds(10),
                interval=cdk.Duration.seconds(30),
                path="/",
                port=params["target_port"],
                healthy_http_codes="200-302",
            ),
            vpc=vpc,
        )

        # Fleet stack
        fleet_instance_role = iam.Role(
            self,
            "FleetInstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
        )
        fleet_instance_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonSSMManagedInstanceCore"
            )
        )
        fleet_instance_role.add_managed_policy(
            iam.ManagedPolicy(
                self,
                "EfsRoPolicy",
                statements=[
                    iam.PolicyStatement(
                        actions=[
                            "elasticfilesystem:ClientMount",
                        ],
                        effect=iam.Effect.ALLOW,
                        resources=[
                            "arn:aws:elasticfilesystem:"
                            + self.region
                            + ":"
                            + self.account
                            + ":file-system/"
                            + efs_fs.file_system_id
                        ],
                        conditions={
                            "Bool": {"elasticfilesystem:AccessedViaMountTarget": "true"}
                        },
                    ),
                    iam.PolicyStatement(
                        actions=[
                            "elasticfilesystem:ClientWrite",
                            "elasticfilesystem:ClientRootAccess",
                        ],
                        effect=iam.Effect.DENY,
                        resources=[
                            "arn:aws:elasticfilesystem:"
                            + self.region
                            + ":"
                            + self.account
                            + ":file-system/"
                            + efs_fs.file_system_id
                        ],
                        conditions={
                            "Bool": {"elasticfilesystem:AccessedViaMountTarget": "true"}
                        },
                    ),
                ],
            )
        )

        NagSuppressions.add_resource_suppressions(
            fleet_instance_role,
            suppressions=[
                NagPackSuppression(
                    id="AwsSolutions-IAM4",
                    reason="Using AWS managed policy to allow for future modifications to the SSM service",
                ),
            ],
        )

        fleet_user_data = ec2.UserData.for_linux()

        fleet_user_data.add_commands(
            *list(map(interpolate_vars, params["fleet_user_data"]))
        )
        if params["fleet_user_data_script"] and path.exists(
            "./userdata/" + params["fleet_user_data_script"]
        ):
            userdata_file = open(
                "./userdata/" + params["fleet_user_data_script"], "rb"
            ).read()
            fleet_user_data.add_commands(interpolate_vars(str(userdata_file, 'utf-8')))

        fleet_asg = autoscaling.AutoScalingGroup(
            self,
            "FleetASG",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            health_check=autoscaling.HealthCheck.elb(
                grace=cdk.Duration.minutes(int(params["fleet_build_time"]))
            ),
            launch_template=ec2.LaunchTemplate(
                self,
                params["app_name"].capitalize()
                + params["environment"].capitalize()
                + "FleetLaunchTemplate",
                role=fleet_instance_role,
                user_data=fleet_user_data,
                ebs_optimized=True,
                machine_image=app_ami,
                security_group=instance_sg,
                instance_type=ec2.InstanceType(
                    instance_type_identifier=params["admin_instance_type"]
                ),
            ),
            min_capacity=params["min_max_fleet_instances"][0],
            max_capacity=params["min_max_fleet_instances"][1],
            notifications=[notification_configuration],
            update_policy=autoscaling.UpdatePolicy.replacing_update(),
        )

        read_targets = elbv2.ApplicationTargetGroup(
            self,
            "FleetTarget",
            targets=[fleet_asg],
            protocol_version=elbv2.ApplicationProtocolVersion.HTTP1,
            protocol=elbv2.ApplicationProtocol.HTTP,
            port=int(params["target_port"]),
            health_check=elbv2.HealthCheck(
                enabled=True,
                unhealthy_threshold_count=5,
                healthy_threshold_count=2,
                timeout=cdk.Duration.seconds(10),
                interval=cdk.Duration.seconds(30),
                path="/",
                port=params["target_port"],
                healthy_http_codes="200,302",
            ),
            vpc=vpc,
        )

        ## ALB

        alb = elbv2.ApplicationLoadBalancer(
            self,
            "ALB",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
            self,
            "MyZone",
            zone_name=params["hosted_zone"],
            hosted_zone_id=params["hosted_zone_id"],
        )

        alb_cert = acm.Certificate(
            self,
            "AlbSiteCertificate",
            domain_name=params["site_hostname"],
            validation=acm.CertificateValidation.from_dns(hosted_zone=hosted_zone),
        )

        alb_listener = alb.add_listener(
            "Listener",
            port=443,
            open=False,
            certificates=[
                acm.Certificate.from_certificate_arn(
                    self, "AlbCert", certificate_arn=alb_cert.certificate_arn
                ),
            ],
            default_action=elbv2.ListenerAction.fixed_response(
                status_code=404,
                message_body="This app can only be accessed via CloudFront.",
            ),
        )

        cloudfront_secret = secretsmanager.Secret(
            self,
            "CloudfrontSecret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                generate_string_key="cloudfront_secret", secret_string_template="{}"
            ),
            replica_regions=[secretsmanager.ReplicaRegion(region="us-east-1")],
            secret_name=params["app_name"]
            + "-"
            + params["environment"]
            + "-"
            + "cloudfront-secret",
        )

        # using unsafe_unwrap here as this secret is not sensitive, and is
        # readable in the CloudFront and ALB consoles anyway
        cloudfront_secret_value = cloudfront_secret.secret_value_from_json(
            json_field="cloudfront_secret"
        ).unsafe_unwrap()

        # get the list of IPs without the subnet mask
        ip_set = []
        for ip in params["admin_ips"]:
            ip_set.append(re.sub('/\d+$', '', ip))

        alb_listener.add_action(
            "ReadAction",
            action=elbv2.ListenerAction.forward(target_groups=[read_targets]),
            conditions=[
                elbv2.ListenerCondition.http_header(
                    name="cloudfront", values=[cloudfront_secret_value]
                )
            ],
            priority=10,
        )

        write_action = alb_listener.add_action(
            "WriteAction",
            action=elbv2.ListenerAction.forward(target_groups=[write_targets]),
            conditions=[
                elbv2.ListenerCondition.http_header(
                    name="cloudfront", values=[cloudfront_secret_value]
                ),
                elbv2.ListenerCondition.http_header('true-client-ip', ip_set),
            ],
            priority=5,
        )

        # to add an authentication action, add something like the following
        # alb_listener.add_action(
        #     "AuthAction",
        #     action=elbv2.ListenerAction.authenticate_oidc(next=write_action, authorization_endpoint=...),
        #     conditions=[
        #         elbv2.ListenerCondition.http_header(
        #             name="cloudfront", values=[params["cloudfront_secret"]]
        #         ),
        #         elbv2.ListenerCondition.http_header('true-client-ip', ip_set),
        #     ],
        #     priority=2,
        # )

        # Custom resource to copy ALB Dns name to us-east-1
        sync_ssm_params_lambda = aws_lambda.Function(
            self,
            "SyncSsmParamsEventHandler",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            architecture=aws_lambda.Architecture.ARM_64,
            handler="sync_params.handler",
            code=aws_lambda.Code.from_asset("custom_resource"),
            dead_letter_queue_enabled=False,
        )

        sync_ssm_params_lambda.add_to_role_policy(
            statement=iam.PolicyStatement(
                actions=[
                    "ssm:PutParameter",
                    "ssm:DeleteParameter",
                ],
                effect=iam.Effect.ALLOW,
                resources=[
                    "arn:aws:ssm:us-east-1:"
                    + self.account
                    + ":parameter"
                    + params["alb_hostname_param"],
                    "arn:aws:ssm:us-east-1:"
                    + self.account
                    + ":parameter"
                    + params["cloudfront_secret_param"],
                ],
            )
        )

        cr_provider_role = iam.Role(
            self,
            "SsmSyncProviderRole",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    managed_policy_name="AWSLambdaExecute"
                )
            ],
            assumed_by=iam.ServicePrincipal(service="lambda.amazonaws.com"),
        )

        cr_provider = cr.Provider(
            self,
            "SsmSyncProvider",
            on_event_handler=sync_ssm_params_lambda,
            log_retention=logs.RetentionDays.ONE_WEEK,  # default is INFINITE
            role=cr_provider_role,
        )

        CustomResource(
            self,
            "SsmSyncCustomResource1",
            service_token=cr_provider.service_token,
            properties={
                "alb_hostname": alb.load_balancer_dns_name,
                "alb_parameter_name": params["alb_hostname_param"],
                "cf_secret_value": cdk.Fn.select(
                    index=6,
                    array=cdk.Fn.split(
                        delimiter=":", source=cloudfront_secret.secret_full_arn
                    ),
                ),
                "cf_parameter_name": params["cloudfront_secret_param"],
            },
        )

        NagSuppressions.add_resource_suppressions(
            cloudfront_secret,
            suppressions=[
                NagPackSuppression(
                    id="AwsSolutions-SMG4",
                    reason="Secret rotation is not needed for this use case (shared secret between CloudFront and ALB)",
                ),
            ],
        )

        NagSuppressions.add_resource_suppressions(
            alb,
            suppressions=[
                NagPackSuppression(
                    id="AwsSolutions-ELB2",
                    reason="Access logs in this scenario are unnecessary as there are no complex configurations to debug, so they would just add cost",
                ),
            ],
        )

        NagSuppressions.add_resource_suppressions(
            admin_instance_role,
            suppressions=[
                NagPackSuppression(
                    id="AwsSolutions-IAM4",
                    reason="Using AWS managed policy to allow for future modifications to the SSM service",
                ),
            ],
        )
