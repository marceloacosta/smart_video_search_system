import os
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    Size,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_apigateway as apigateway,
    aws_logs as logs,
    aws_s3_notifications as s3n,
)
from constructs import Construct

class InfrastructureStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, project_name: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        self.project_name = project_name
        account = Stack.of(self).account
        region = Stack.of(self).region
        
        # ======================
        # CONFIGURATION
        # ======================
        # These should be provided via cdk.context.json or environment variables
        speech_kb_id = self.node.try_get_context("speech_kb_id") or os.getenv("SPEECH_KB_ID")
        caption_kb_id = self.node.try_get_context("caption_kb_id") or os.getenv("CAPTION_KB_ID")
        speech_ds_id = self.node.try_get_context("speech_ds_id") or os.getenv("SPEECH_DS_ID")
        caption_ds_id = self.node.try_get_context("caption_ds_id") or os.getenv("CAPTION_DS_ID")
        agentcore_api_url = self.node.try_get_context("agentcore_api_url") or os.getenv("AGENTCORE_API_URL")
        
        if not all([speech_kb_id, caption_kb_id]):
            print("⚠️  Warning: Knowledge Base IDs not configured. Set via cdk.context.json or environment variables.")
            print("   Required: speech_kb_id, caption_kb_id, speech_ds_id, caption_ds_id")
        
        # ======================
        # S3 BUCKETS
        # ======================
        self.raw_bucket = s3.Bucket(
            self,
            "VideosRawBucket",
            bucket_name=f"{project_name}-videos-raw-{account}-{region}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="CleanupTestVideos",
                    prefix="test/",
                    expiration=Duration.days(30),
                    enabled=True
                )
            ]
        )
        
        self.processed_bucket = s3.Bucket(
            self,
            "ProcessedBucket",
            bucket_name=f"{project_name}-processed-{account}-{region}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        
        # ======================
        # DYNAMODB TABLE
        # ======================
        self.video_table = dynamodb.Table(
            self,
            "VideoMetadataTable",
            table_name=f"{project_name}-video-metadata",
            partition_key=dynamodb.Attribute(
                name="video_id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
        )
        
        # Add GSI for status queries
        self.video_table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL
        )
        
        # ======================
        # IAM ROLE FOR LAMBDAS
        # ======================
        lambda_role = iam.Role(
            self,
            "LambdaExecutionRole",
            role_name=f"{project_name}-lambda-execution-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AWSXRayDaemonWriteAccess"),
            ]
        )
        
        # Grant S3 permissions
        self.raw_bucket.grant_read_write(lambda_role)
        self.processed_bucket.grant_read_write(lambda_role)
        
        # Grant DynamoDB permissions
        self.video_table.grant_read_write_data(lambda_role)
        
        # Grant Transcribe permissions
        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "transcribe:StartTranscriptionJob",
                "transcribe:GetTranscriptionJob",
                "transcribe:DeleteTranscriptionJob"
            ],
            resources=["*"]
        ))
        
        # Grant Bedrock permissions
        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream"
            ],
            resources=[
                f"arn:aws:bedrock:*:{account}:inference-profile/*",
                "arn:aws:bedrock:*::foundation-model/amazon.titan-*",
                "arn:aws:bedrock:*::foundation-model/anthropic.claude-*"
            ]
        ))
        
        # Grant Bedrock Knowledge Base permissions
        if speech_kb_id and caption_kb_id:
            lambda_role.add_to_policy(iam.PolicyStatement(
                actions=[
                    "bedrock:Retrieve",
                    "bedrock:RetrieveAndGenerate"
                ],
                resources=[
                    f"arn:aws:bedrock:{region}:{account}:knowledge-base/{speech_kb_id}",
                    f"arn:aws:bedrock:{region}:{account}:knowledge-base/{caption_kb_id}"
                ]
            ))
            
            lambda_role.add_to_policy(iam.PolicyStatement(
                actions=[
                    "bedrock:StartIngestionJob",
                    "bedrock:GetIngestionJob",
                    "bedrock:ListIngestionJobs"
                ],
                resources=[f"arn:aws:bedrock:{region}:{account}:knowledge-base/*"]
            ))
        
        # Grant Lambda invoke permissions (for async invocations)
        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=["lambda:InvokeFunction"],
            resources=[f"arn:aws:lambda:{region}:{account}:function:{project_name}-*"]
        ))
        
        # ======================
        # COMMON LAMBDA ENVIRONMENT
        # ======================
        common_env = {
            "RAW_BUCKET": self.raw_bucket.bucket_name,
            "PROCESSED_BUCKET": self.processed_bucket.bucket_name,
            "VIDEO_TABLE": self.video_table.table_name,
            "REGION": region,
        }
        
        if speech_kb_id:
            common_env["SPEECH_KB_ID"] = speech_kb_id
        if caption_kb_id:
            common_env["CAPTION_KB_ID"] = caption_kb_id
        if speech_ds_id:
            common_env["SPEECH_DS_ID"] = speech_ds_id
        if caption_ds_id:
            common_env["CAPTION_DS_ID"] = caption_ds_id
        if agentcore_api_url:
            common_env["AGENTCORE_API_URL"] = agentcore_api_url
        
        # ======================
        # LAMBDA FUNCTIONS
        # ======================
        # Path to Lambda source code
        lambda_code_path = "../src/lambdas"
        
        # 1. Process Video (orchestrator)
        process_video_fn = lambda_.Function(
            self,
            "ProcessVideoFunction",
            function_name=f"{project_name}-process-video",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="process_video.handler",
            code=lambda_.Code.from_asset(lambda_code_path),
            role=lambda_role,
            timeout=Duration.seconds(300),
            memory_size=512,
            environment=common_env,
            log_retention=logs.RetentionDays.TWO_YEARS,
        )
        
        # 2. Check Transcription (polling)
        check_transcription_fn = lambda_.Function(
            self,
            "CheckTranscriptionFunction",
            function_name=f"{project_name}-check-transcription",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="check_transcription.handler",
            code=lambda_.Code.from_asset(lambda_code_path),
            role=lambda_role,
            timeout=Duration.seconds(60),
            memory_size=256,
            environment={
                **common_env,
                "POLL_INTERVAL_SECONDS": "30",
                "MAX_ATTEMPTS": "60"
            },
            log_retention=logs.RetentionDays.TWO_YEARS,
        )
        
        # 3. Extract Frames
        extract_frames_fn = lambda_.Function(
            self,
            "ExtractFramesFunction",
            function_name=f"{project_name}-extract-frames",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="extract_frames.handler",
            code=lambda_.Code.from_asset(lambda_code_path),
            role=lambda_role,
            timeout=Duration.seconds(600),
            memory_size=2048,
            ephemeral_storage_size=Size.mebibytes(2048),
            environment={
                **common_env,
                "MAX_FRAMES": "120",
                "QUALITY": "85"
            },
            log_retention=logs.RetentionDays.TWO_YEARS,
        )
        
        # 4. Generate Captions
        generate_captions_fn = lambda_.Function(
            self,
            "GenerateCaptionsFunction",
            function_name=f"{project_name}-generate-captions",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="generate_captions.handler",
            code=lambda_.Code.from_asset(lambda_code_path),
            role=lambda_role,
            timeout=Duration.seconds(900),
            memory_size=1024,
            environment={
                **common_env,
                "BEDROCK_MODEL_ID": "anthropic.claude-3-5-sonnet-20241022-v2:0"
            },
            log_retention=logs.RetentionDays.TWO_YEARS,
        )
        
        # 5. Chunk Transcript
        chunk_transcript_fn = lambda_.Function(
            self,
            "ChunkTranscriptFunction",
            function_name=f"{project_name}-chunk-transcript",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="chunk_transcript.handler",
            code=lambda_.Code.from_asset(lambda_code_path),
            role=lambda_role,
            timeout=Duration.seconds(300),
            memory_size=512,
            environment=common_env,
            log_retention=logs.RetentionDays.TWO_YEARS,
        )
        
        # 6. Embed Captions
        embed_captions_fn = lambda_.Function(
            self,
            "EmbedCaptionsFunction",
            function_name=f"{project_name}-embed-captions",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="embed_captions.handler",
            code=lambda_.Code.from_asset(lambda_code_path),
            role=lambda_role,
            timeout=Duration.seconds(300),
            memory_size=512,
            environment=common_env,
            log_retention=logs.RetentionDays.TWO_YEARS,
        )
        
        # 7. Embed and Index Images (S3 Vectors)
        embed_index_images_fn = lambda_.Function(
            self,
            "EmbedAndIndexImagesFunction",
            function_name=f"{project_name}-embed-and-index-images",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="embed_and_index_images.handler",
            code=lambda_.Code.from_asset(lambda_code_path),
            role=lambda_role,
            timeout=Duration.seconds(600),
            memory_size=1024,
            environment={
                **common_env,
                "EMBEDDING_MODEL_ID": "amazon.titan-embed-image-v1"
            },
            log_retention=logs.RetentionDays.TWO_YEARS,
        )
        
        # 8. Search Images (standalone)
        search_images_fn = lambda_.Function(
            self,
            "SearchImagesFunction",
            function_name=f"{project_name}-search-images",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="search_images.handler",
            code=lambda_.Code.from_asset(lambda_code_path),
            role=lambda_role,
            timeout=Duration.seconds(30),
            memory_size=512,
            environment=common_env,
            log_retention=logs.RetentionDays.TWO_YEARS,
        )
        
        # 9-14. MCP Tool Functions
        tool_list_videos_fn = self._create_tool_function("list-videos", "list_videos", lambda_role, common_env)
        tool_get_metadata_fn = self._create_tool_function("get-video-metadata", "get_video_metadata", lambda_role, common_env)
        tool_get_transcript_fn = self._create_tool_function("get-full-transcript", "get_full_transcript", lambda_role, common_env)
        tool_search_speech_fn = self._create_tool_function("search-by-speech", "search_by_speech", lambda_role, common_env)
        tool_search_caption_fn = self._create_tool_function("search-by-caption", "search_by_caption", lambda_role, common_env)
        tool_search_image_fn = self._create_tool_function("search-by-image", "search_by_image", lambda_role, common_env)
        
        # 15. Upload/Management Functions
        get_upload_url_fn = lambda_.Function(
            self,
            "GetUploadUrlFunction",
            function_name=f"{project_name}-get-upload-url",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="get_upload_url.handler",
            code=lambda_.Code.from_asset(lambda_code_path),
            role=lambda_role,
            timeout=Duration.seconds(30),
            memory_size=256,
            environment=common_env,
            log_retention=logs.RetentionDays.TWO_YEARS,
        )
        
        upload_video_fn = lambda_.Function(
            self,
            "UploadVideoFunction",
            function_name=f"{project_name}-upload-video",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="upload_video.handler",
            code=lambda_.Code.from_asset(lambda_code_path),
            role=lambda_role,
            timeout=Duration.seconds(30),
            memory_size=256,
            environment=common_env,
            log_retention=logs.RetentionDays.TWO_YEARS,
        )
        
        delete_video_fn = lambda_.Function(
            self,
            "DeleteVideoFunction",
            function_name=f"{project_name}-delete-video",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="delete_video.handler",
            code=lambda_.Code.from_asset(lambda_code_path),
            role=lambda_role,
            timeout=Duration.seconds(60),
            memory_size=256,
            environment=common_env,
            log_retention=logs.RetentionDays.TWO_YEARS,
        )
        
        # 16. Agent API
        agent_api_fn = lambda_.Function(
            self,
            "AgentApiFunction",
            function_name=f"{project_name}-agent-api",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="agent_api.handler",
            code=lambda_.Code.from_asset(lambda_code_path),
            role=lambda_role,
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={
                **common_env,
                "AGENT_MODEL_ID": "anthropic.claude-sonnet-4-20250514-v1:0"
            },
            log_retention=logs.RetentionDays.TWO_YEARS,
        )
        
        # ======================
        # S3 TRIGGERS
        # ======================
        # Trigger process_video when .mp4 uploaded to raw bucket
        self.raw_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(process_video_fn),
            s3.NotificationKeyFilter(suffix=".mp4")
        )
        
        # ======================
        # API GATEWAY
        # ======================
        api = apigateway.RestApi(
            self,
            "VideoSearchApi",
            rest_api_name=f"{project_name}-video-search-api",
            description="Smart Video Search System API",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization"]
            ),
        )
        
        # /query endpoint
        query_resource = api.root.add_resource("query")
        query_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(agent_api_fn)
        )
        
        # /upload endpoint
        upload_resource = api.root.add_resource("upload")
        upload_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(get_upload_url_fn)
        )
        
        # /videos endpoint
        videos_resource = api.root.add_resource("videos")
        videos_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(tool_list_videos_fn)
        )
        
        # /videos/{video_id} endpoint
        video_resource = videos_resource.add_resource("{video_id}")
        video_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(tool_get_metadata_fn)
        )
        video_resource.add_method(
            "DELETE",
            apigateway.LambdaIntegration(delete_video_fn)
        )
        
        # ======================
        # OUTPUTS
        # ======================
        self.api_endpoint = api.url
        
        CfnOutput(
            self,
            "ApiEndpoint",
            value=api.url,
            description="API Gateway endpoint URL"
        )
        
        CfnOutput(
            self,
            "RawBucketName",
            value=self.raw_bucket.bucket_name,
            description="S3 bucket for raw videos"
        )
        
        CfnOutput(
            self,
            "ProcessedBucketName",
            value=self.processed_bucket.bucket_name,
            description="S3 bucket for processed data"
        )
        
        CfnOutput(
            self,
            "VideoTableName",
            value=self.video_table.table_name,
            description="DynamoDB table for video metadata"
        )
    
    def _create_tool_function(self, name: str, handler_file: str, role: iam.Role, env: dict) -> lambda_.Function:
        """Helper to create MCP tool Lambda functions"""
        return lambda_.Function(
            self,
            f"Tool{name.replace('-', '').title()}Function",
            function_name=f"{self.project_name}-tool-{name}",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler=f"tools/{handler_file}.handler",
            code=lambda_.Code.from_asset("../src/lambdas"),
            role=role,
            timeout=Duration.seconds(30),
            memory_size=512,
            environment=env,
            log_retention=logs.RetentionDays.TWO_YEARS,
        )

