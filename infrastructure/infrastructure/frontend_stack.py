from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    Duration,
    aws_s3 as s3,
    aws_s3_deployment as s3_deploy,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
)
from constructs import Construct

class FrontendStack(Stack):
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str, 
        project_name: str,
        api_endpoint: str,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        account = Stack.of(self).account
        region = Stack.of(self).region
        
        # ======================
        # S3 BUCKET FOR FRONTEND
        # ======================
        self.frontend_bucket = s3.Bucket(
            self,
            "FrontendBucket",
            bucket_name=f"{project_name}-frontend-{account}-{region}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        
        # ======================
        # CLOUDFRONT DISTRIBUTION
        # ======================
        # Origin Access Identity for S3
        oai = cloudfront.OriginAccessIdentity(
            self,
            "FrontendOAI",
            comment=f"{project_name} frontend OAI"
        )
        
        # Grant OAI read access to bucket
        self.frontend_bucket.grant_read(oai)
        
        # CloudFront distribution
        self.distribution = cloudfront.Distribution(
            self,
            "FrontendDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(
                    self.frontend_bucket,
                    origin_access_identity=oai
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5)
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5)
                )
            ],
            comment=f"{project_name} frontend distribution"
        )
        
        # ======================
        # DEPLOY FRONTEND FILES
        # ======================
        # Deploy the frontend from agent/web/ directory
        s3_deploy.BucketDeployment(
            self,
            "DeployFrontend",
            sources=[s3_deploy.Source.asset("../agent/web")],
            destination_bucket=self.frontend_bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
        )
        
        # ======================
        # OUTPUTS
        # ======================
        CfnOutput(
            self,
            "WebsiteURL",
            value=f"https://{self.distribution.distribution_domain_name}",
            description="CloudFront URL for the frontend"
        )
        
        CfnOutput(
            self,
            "FrontendBucketName",
            value=self.frontend_bucket.bucket_name,
            description="S3 bucket hosting the frontend"
        )
        
        CfnOutput(
            self,
            "DistributionId",
            value=self.distribution.distribution_id,
            description="CloudFront distribution ID"
        )

