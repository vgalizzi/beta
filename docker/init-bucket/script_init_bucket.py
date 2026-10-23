# /// script
# requires-python = ">=3.12"
# dependencies = ["boto3"]
# ///

import logging
import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# A single channel: everything goes through logging, hence to stderr, unbuffered.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("bucket-init")

REQUIRED_VARS = ("STORAGE_URL_INTERNAL", "BUCKET_NAME", "STORAGE_ACCESS_KEY", "STORAGE_SECRET_KEY")

# Compose replaces a variable missing from .env with an empty string,
# so an empty value counts as missing.
missing = [name for name in REQUIRED_VARS if not os.environ.get(name)]
if missing:
    logger.error("Missing environment variables: %s", ", ".join(missing))
    sys.exit(1)

bucket_name = os.environ["BUCKET_NAME"]
s3_client = boto3.client(
    "s3",
    endpoint_url=os.environ["STORAGE_URL_INTERNAL"],
    aws_access_key_id=os.environ["STORAGE_ACCESS_KEY"],
    aws_secret_access_key=os.environ["STORAGE_SECRET_KEY"],
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)

try:
    s3_client.head_bucket(Bucket=bucket_name)
    logger.info("Bucket %r already exists", bucket_name)
except ClientError as e:
    # Only a 404 means "absent". A 403 almost always comes from the credentials.
    if e.response["Error"]["Code"] != "404":
        logger.error("Could not check bucket %r: %s", bucket_name, e)
        sys.exit(1)
    s3_client.create_bucket(Bucket=bucket_name)
    logger.info("Bucket %r created", bucket_name)
