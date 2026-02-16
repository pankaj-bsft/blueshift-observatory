# Configuration settings for MBR Dashboard
import os
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables from .env file in parent directory
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

DRUID_US_BROKER = 'http://druid.blueshift.vpc/druid/v2/sql/'
DRUID_EU_BROKER = 'http://druid-1.prodeu.vpc/druid/v2/sql/'

ESPS = ['Sparkpost', 'Sendgrid', 'Mailgun']

# ESP API Credentials for Account Info (loaded from environment)
MAILGUN_API_KEY = os.getenv('MAILGUN_API_KEY', 'key-067d89fed50025263a19c5c4410856e6')
MAILGUN_US_BASE_URL = 'https://api.mailgun.net/v3'
MAILGUN_EU_BASE_URL = 'https://api.eu.mailgun.net/v3'

SPARKPOST_API_KEY = os.getenv('SPARKPOST_API_KEY', '7561ca5db97fd8866d9112eb4781154486b18971')
SPARKPOST_BASE_URL = 'https://api.sparkpost.com/api/v1'

SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')
SENDGRID_BASE_URL = 'https://api.sendgrid.com/v3'

DRUID_QUERY_TEMPLATE = """
SELECT
  MV_OFFSET(STRING_TO_MV(LOOKUP("extended_attributes.adapter_uuid", 'accountadapters_uuid-to-accountadapters_from_address'), '@'), 1) AS "From_domain",
  sum(case action when 'sent' then "count" else null end) as Sent,
  sum(case action when 'delivered' then "count" else null end) as Delivered,
  APPROX_COUNT_DISTINCT_DS_HLL(CASE WHEN action ='open' AND "extended_attributes.opened_by" = 'user' then "message_distinct" else null end) as Unique_user_open,
  APPROX_COUNT_DISTINCT_DS_HLL(CASE WHEN action ='open' AND "extended_attributes.opened_by" = 'pre-fetch' then "message_distinct" else null end) as Unique_pre_fetch_open,
  APPROX_COUNT_DISTINCT_DS_HLL(CASE WHEN action ='open' AND "extended_attributes.opened_by" = 'proxy' then "message_distinct" else null end) as Unique_proxy_open,
  sum(case action when 'click' then "count" else null end) as Clicks,
  APPROX_COUNT_DISTINCT_DS_HLL(CASE WHEN action ='click'  then "message_distinct" else null end) as unique_click,
  sum(case action when 'bounce' then "count" else null end) as Bounces,
  APPROX_COUNT_DISTINCT_DS_HLL(case action when 'soft_bounce' then "message_distinct" else null end) as Unique_soft_bounce,
  sum(case action when 'spam_report' then "count" else null end) as Spam_report,
  sum(case action when 'unsubscribe' then "count" else null end) as Unsubscribe,
  LOOKUP(LOOKUP("extended_attributes.adapter_uuid", 'accountadapters_uuid-to-accountadapters_adapter_id'),'adapters_id-to-adapters_name') AS "ESP"
FROM ucts_1
WHERE "__time" >= TIMESTAMP '{start_date}'
 AND "__time" < TIMESTAMP '{end_date}'
 AND LOOKUP("extended_attributes.adapter_uuid", 'accountadapters_uuid-to-accountadapters_from_address') IS NOT NULL
 AND LOOKUP(LOOKUP("extended_attributes.adapter_uuid", 'accountadapters_uuid-to-accountadapters_adapter_id'),'adapters_id-to-adapters_name') IN ('Sparkpost','Mailgun','Sendgrid')
GROUP BY
 LOOKUP(LOOKUP("extended_attributes.adapter_uuid", 'accountadapters_uuid-to-accountadapters_adapter_id'),'adapters_id-to-adapters_name'),
 MV_OFFSET(STRING_TO_MV(LOOKUP("extended_attributes.adapter_uuid", 'accountadapters_uuid-to-accountadapters_from_address'), '@'), 1)
"""
