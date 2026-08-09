#!/bin/bash

# Interactive build script for AriaCast Go binary

set -e

cd "$(dirname "$0")"

# Get the target directory
BIN_DIR="bin"
mkdir -p "$BIN_DIR"

# Color codes for better UX
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}🔨 AriaCast Build System${NC}"
echo ""
echo "Select platform(s) to build:"
echo ""
echo "  ${GREEN}1)${NC}  macOS Intel (darwin/amd64)"
echo "  ${GREEN}2)${NC}  macOS Apple Silicon (darwin/arm64)"
echo "  ${GREEN}3)${NC}  Linux x64 (linux/amd64)"
echo "  ${GREEN}4)${NC}  Linux ARM64 (linux/arm64)"
echo "  ${GREEN}5)${NC}  Linux ARM (linux/arm)"
echo "  ${GREEN}6)${NC}  Windows x64 (windows/amd64)"
echo "  ${GREEN}7)${NC}  Windows ARM64 (windows/arm64)"
echo ""
echo "  ${YELLOW}8)${NC}  ALL platforms"
echo "  ${YELLOW}9)${NC}  ALL Linux"
echo "  ${YELLOW}10)${NC} ALL macOS"
echo "  ${YELLOW}11)${NC} ALL Windows"
echo ""
echo -n "Enter your choice (e.g., 1 or 1,3,5 or 8): "
read CHOICE

# Function to build for a specific platform
build_platform() {
    local PLATFORM=$1
    OS="${PLATFORM%/*}"
    ARCH="${PLATFORM#*/}"
    
    if [ "$OS" = "windows" ]; then
        OUTPUT="$BIN_DIR/ariacast_${OS}_${ARCH}.exe"
    else
        OUTPUT="$BIN_DIR/ariacast_${OS}_${ARCH}"
    fi
    
    echo -e "${BLUE}📦 Building for $OS/$ARCH...${NC}"
    GOOS=$OS GOARCH=$ARCH go build -o "$OUTPUT" main.go
    
    if [ $? -eq 0 ]; then
        # Make executable (not needed for Windows)
        if [ "$OS" != "windows" ]; then
            chmod +x "$OUTPUT"
        fi
        echo -e "${GREEN}✅ Built: $OUTPUT${NC}"
        return 0
    else
        echo -e "${RED}❌ Failed to build for $OS/$ARCH${NC}"
        return 1
    fi
}

# Map choices to platforms
declare -A PLATFORM_MAP
PLATFORM_MAP[1]="darwin/amd64"
PLATFORM_MAP[2]="darwin/arm64"
PLATFORM_MAP[3]="linux/amd64"
PLATFORM_MAP[4]="linux/arm64"
PLATFORM_MAP[5]="linux/arm"
PLATFORM_MAP[6]="windows/amd64"
PLATFORM_MAP[7]="windows/arm64"

# Array to store selected platforms
SELECTED_PLATFORMS=()

# Parse user choice
if [ "$CHOICE" = "8" ]; then
    # ALL platforms
    SELECTED_PLATFORMS=("darwin/amd64" "darwin/arm64" "linux/amd64" "linux/arm64" "linux/arm" "windows/amd64" "windows/arm64")
elif [ "$CHOICE" = "9" ]; then
    # ALL Linux
    SELECTED_PLATFORMS=("linux/amd64" "linux/arm64" "linux/arm")
elif [ "$CHOICE" = "10" ]; then
    # ALL macOS
    SELECTED_PLATFORMS=("darwin/amd64" "darwin/arm64")
elif [ "$CHOICE" = "11" ]; then
    # ALL Windows
    SELECTED_PLATFORMS=("windows/amd64" "windows/arm64")
else
    # Parse comma-separated choices
    IFS=',' read -ra CHOICES <<< "$CHOICE"
    for choice in "${CHOICES[@]}"; do
        # Trim whitespace
        choice=$(echo "$choice" | xargs)
        if [ -n "${PLATFORM_MAP[$choice]}" ]; then
            SELECTED_PLATFORMS+=("${PLATFORM_MAP[$choice]}")
        else
            echo -e "${YELLOW}⚠️  Invalid choice: $choice (skipping)${NC}"
        fi
    done
fi

# Check if any platforms were selected
if [ ${#SELECTED_PLATFORMS[@]} -eq 0 ]; then
    echo -e "${RED}❌ No valid platforms selected. Exiting.${NC}"
    exit 1
fi

echo ""
echo -e "${BLUE}Building ${#SELECTED_PLATFORMS[@]} platform(s)...${NC}"
echo ""

# Build for each selected platform
SUCCESS_COUNT=0
FAIL_COUNT=0

for PLATFORM in "${SELECTED_PLATFORMS[@]}"; do
    if build_platform "$PLATFORM"; then
        ((SUCCESS_COUNT++))
    else
        ((FAIL_COUNT++))
    fi
    echo ""
done

# Summary
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}✅ Successfully built: $SUCCESS_COUNT${NC}"
if [ $FAIL_COUNT -gt 0 ]; then
    echo -e "${RED}❌ Failed: $FAIL_COUNT${NC}"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ $SUCCESS_COUNT -gt 0 ]; then
    echo "Built binaries:"
    ls -lh "$BIN_DIR"/ariacast_* 2>/dev/null || echo "(no files found)"
fi

echo ""
echo -e "${BLUE}🎉 Build complete!${NC}"
