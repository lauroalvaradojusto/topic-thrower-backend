#!/bin/bash
# Deploy Edge Functions to Supabase
# Usage: ./deploy.sh [function-name]

set -e

SUPABASE_PROJECT_ID="zehaldntdigaiakhjasi"
FUNCTIONS_DIR="edge-functions"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Hermes Edge Functions Deploy ===${NC}"
echo ""

# Check if supabase CLI is installed
if ! command -v supabase &> /dev/null; then
    echo -e "${YELLOW}Supabase CLI not found. Installing...${NC}"
    npm install -g supabase
fi

# Login check
echo -e "${YELLOW}Checking Supabase authentication...${NC}"
if ! supabase projects list &> /dev/null; then
    echo -e "${RED}Not logged in to Supabase. Run: supabase login${NC}"
    exit 1
fi

# Link project if not already linked
if [ ! -f ".supabase/config.toml" ]; then
    echo -e "${YELLOW}Linking project...${NC}"
    supabase link --project-ref "$SUPABASE_PROJECT_ID"
fi

# Deploy functions
deploy_function() {
    local func_name=$1
    echo -e "${GREEN}Deploying $func_name...${NC}"
    supabase functions deploy "$func_name" --project-ref "$SUPABASE_PROJECT_ID"
    echo -e "${GREEN}✓ $func_name deployed${NC}"
    echo ""
}

if [ -n "$1" ]; then
    # Deploy specific function
    deploy_function "$1"
else
    # Deploy all functions
    for dir in "$FUNCTIONS_DIR"/*/; do
        func_name=$(basename "$dir")
        deploy_function "$func_name"
    done
fi

echo -e "${GREEN}=== Deployment Complete ===${NC}"
echo ""
echo "Functions deployed:"
echo "  - chat-deepseek: https://${SUPABASE_PROJECT_ID}.supabase.co/functions/v1/chat-deepseek"
echo "  - process-topic: https://${SUPABASE_PROJECT_ID}.supabase.co/functions/v1/process-topic"
echo ""
echo "Set these environment variables in Supabase Dashboard:"
echo "  - HERMES_BACKEND_URL: https://hermes-api-production-1195.up.railway.app"
echo "  - HERMES_API_KEY: (from your .env)"
echo "  - DEEPSEEK_API_KEY: (your DeepSeek API key)"