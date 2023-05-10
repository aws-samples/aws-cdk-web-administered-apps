# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from aws_cdk import (
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as cloudfront_origins,
    aws_wafv2 as wafv2,
    aws_s3 as s3,
    aws_route53 as route53,
    aws_certificatemanager as acm,
    aws_ssm as ssm,
    aws_secretsmanager as secretsmanager,
)

import aws_cdk as cdk

from constructs import Construct
from cdk_nag import NagSuppressions, NagPackSuppression

import jsii


@jsii.implements(wafv2.CfnRuleGroup.IPSetReferenceStatementProperty)
class IPSetReferenceStatement:
    @property
    def arn(self):
        return self._arn

    @arn.setter
    def arn(self, value):
        self._arn = value


class CdnStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, params: map, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
            self,
            "MyZone",
            zone_name=params["hosted_zone"],
            hosted_zone_id=params["hosted_zone_id"],
        )

        cloudfront_web_cert = acm.Certificate(
            self,
            "WebCertificate",
            domain_name=params["site_hostname"],
            validation=acm.CertificateValidation.from_dns(hosted_zone=hosted_zone),
        )

        waf_rules = []
        managed_rules = params["managed_waf_rules"]
        rule_count = 1
        for rule in managed_rules:
            waf_rules.append(
                wafv2.CfnWebACL.RuleProperty(
                    name="AWS-" + rule,
                    priority=rule_count,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name=rule,
                        )
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name="AWS-" + rule,
                    ),
                )
            )
            rule_count = rule_count + 1

        # with AWS managed rules rely on them to block as they see fit and allow the rest
        waf_default_action = wafv2.CfnWebACL.DefaultActionProperty(allow={})

        if params["allowed_ips"][0] != "*":
            # with an IP allow list we need to block any requests that don't match
            waf_default_action = wafv2.CfnWebACL.DefaultActionProperty(block={})
            permitted_ips_v4 = wafv2.CfnIPSet(
                self,
                "IPSetv4",
                addresses=params["allowed_ips"],
                ip_address_version="IPV4",
                scope="CLOUDFRONT",
            )
            ip_allow_list = wafv2.CfnWebACL.RuleProperty(
                name="Permitted-IPs",
                priority=rule_count,
                action=wafv2.CfnWebACL.RuleActionProperty(allow={}),
                visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                    sampled_requests_enabled=True,
                    cloud_watch_metrics_enabled=True,
                    metric_name="allow-permitted-ips",
                ),
                statement=wafv2.CfnWebACL.StatementProperty(
                    ip_set_reference_statement={"arn": permitted_ips_v4.attr_arn}
                ),
            )
            waf_rules.append(ip_allow_list)

        waf = wafv2.CfnWebACL(
            self,
            "CloudFrontWebACL",
            ####################################################################################
            # Set this to allow|block to enable|prevent access to requests not matching a rule
            ####################################################################################
            default_action=waf_default_action,
            scope="CLOUDFRONT",
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="WAF",
                sampled_requests_enabled=True,
            ),
            rules=waf_rules,
        )

        if params["forwarded_cookies"][0] == "*":
            cookie_behaviour = cloudfront.OriginRequestCookieBehavior.all()
        else:
            cookie_behaviour = cloudfront.OriginRequestCookieBehavior.allow_list(
                *params["forwarded_cookies"]
            )

        cf_origin_req_policy_headers = cloudfront.OriginRequestPolicy(
            self,
            "OriginReqPolicyHeaders",
            header_behavior=cloudfront.OriginRequestHeaderBehavior.allow_list(
                "Host",
                "Origin",
                "Referer",
                "CloudFront-Is-Desktop-Viewer",
                "CloudFront-Is-Mobile-Viewer",
                "CloudFront-Is-Tablet-Viewer",
                "true-client-ip",
            ),
            cookie_behavior=cookie_behaviour,
            query_string_behavior=cloudfront.OriginRequestQueryStringBehavior.all(),
        )

        cf_origin_req_policy_headers_nocache = cloudfront.OriginRequestPolicy(
            self,
            "OriginReqPolicyHeadersNoCache",
            header_behavior=cloudfront.OriginRequestHeaderBehavior.all(),
            cookie_behavior=cloudfront.OriginRequestCookieBehavior.all(),
            query_string_behavior=cloudfront.OriginRequestQueryStringBehavior.all(),
        )

        cf_cache_policy = cloudfront.CachePolicy(
            self,
            "WpCachePolicy",
            cache_policy_name=params["app_name"]
            + "-"
            + params["environment"]
            + "-cache-policy",
            query_string_behavior=cloudfront.CacheQueryStringBehavior.all(),
            min_ttl=cdk.Duration.seconds(1),
            max_ttl=cdk.Duration.seconds(31536000),
            default_ttl=cdk.Duration.seconds(86400),
            enable_accept_encoding_gzip=True,
            enable_accept_encoding_brotli=False,
        )

        cf_dist_bucket = s3.Bucket(
            self,
            "CloudFrontLogBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            access_control=s3.BucketAccessControl.BUCKET_OWNER_FULL_CONTROL,
        )

        alb_hostname = ssm.StringParameter.value_from_lookup(
            self, parameter_name=params["alb_hostname_param"]
        )

        cf_secret_arn = ssm.StringParameter.from_string_parameter_name(
            self,
            "SecretNameParam",
            string_parameter_name=params["cloudfront_secret_param"],
        )

        cloudfront_secret = secretsmanager.Secret.from_secret_attributes(
            self,
            "CloudfrontSecret",
            secret_complete_arn="arn:aws:secretsmanager:us-east-1:"
            + self.account
            + ":secret:"
            + cf_secret_arn.string_value,
        )

        # using unsafe_unwrap here as this secret is not sensitive, and is
        # readable in the CloudFront and ALB consoles anyway
        cloudfront_secret_value = cloudfront_secret.secret_value_from_json(
            key="cloudfront_secret"
        ).unsafe_unwrap()

        request_origin = cloudfront_origins.HttpOrigin(
            domain_name=alb_hostname,
            origin_ssl_protocols=[cloudfront.OriginSslPolicy.TLS_V1_2],
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
            custom_headers={"cloudfront": cloudfront_secret_value},
            keepalive_timeout=cdk.Duration.seconds(60),
        )

        ip_function = cloudfront.Function(
            self,
            "IpFunction",
            code=cloudfront.FunctionCode.from_inline(
                code="""
function handler(event) {
    var request = event.request;
    var clientIP = event.viewer.ip;

    //Add the true-client-ip header to the incoming request
    request.headers['true-client-ip'] = {value: clientIP};

    return request;
}
        """
            ),
        )

        cf_dist = cloudfront.Distribution(
            self,
            "CloudFrontDistribution",
            log_bucket=cf_dist_bucket,
            web_acl_id=waf.attr_arn,
            certificate=cloudfront_web_cert,
            domain_names=[params["site_hostname"]],
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            enable_logging=True,
            default_behavior=cloudfront.BehaviorOptions(
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                compress=True,
                cache_policy=cf_cache_policy,
                origin=request_origin,
                origin_request_policy=cf_origin_req_policy_headers,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                        function=ip_function,
                    )
                ],
            ),
            default_root_object="",
        )

        for path in params["uncached_paths"]:
            cf_dist.add_behavior(
                path_pattern=path,
                origin=request_origin,
                compress=True,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cache_policy=cf_cache_policy,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
                origin_request_policy=cf_origin_req_policy_headers_nocache,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                        function=ip_function,
                    )
                ],
            )

        route53.CfnRecordSet(
            self,
            "MainRecordset",
            type="A",
            alias_target=route53.CfnRecordSet.AliasTargetProperty(
                dns_name=cf_dist.domain_name, hosted_zone_id="Z2FDTNDATAQYW2"
            ),
            name=params["site_hostname"],
            hosted_zone_id=params["hosted_zone_id"],
        )

        NagSuppressions.add_resource_suppressions(
            cf_dist_bucket,
            suppressions=[
                NagPackSuppression(
                    id="AwsSolutions-S1",
                    reason="Enable if you need it, but this seems unnecessary for a logging bucket",
                ),
            ],
        )
