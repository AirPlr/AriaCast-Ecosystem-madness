"""
AudioCast Protocol Server - Quick Start Script
Simple launcher with common options.
"""

import asyncio
import sys
import argparse
import logging

from main import AudioCastServer, ServerConfig
from config_examples import get_config


def setup_logging(verbose: bool = False):
    """Setup logging with optional verbose output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


async def run_server(config: ServerConfig, verbose: bool = False):
    """Run the AudioCast server with given configuration."""
    setup_logging(verbose)
    
    logger = logging.getLogger(__name__)
    
    try:
        server = AudioCastServer(config, verbose=verbose)
        await server.start()
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=verbose)
        return 1
    
    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='AudioCast Protocol Server',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python start.py                     # Run with default configuration
  python start.py -c living_room      # Run living room speaker config
  python start.py -n "Kitchen" -p 12890  # Custom name and port
  python start.py -v                  # Verbose logging
  python start.py --list-configs      # List available configurations

Available Configurations:
  basic         - Default configuration
  living_room   - Living room speaker (RaspberryPi)
  bedroom       - Bedroom speaker with low latency
  studio        - Studio monitor (96kHz, high quality)
  mono          - Budget speaker (mono, 16kHz)
  cloud         - Cloud streaming with high latency
        """
    )
    
    parser.add_argument(
        '-c', '--config',
        type=str,
        default='basic',
        help='Configuration preset (default: basic)'
    )
    
    parser.add_argument(
        '-n', '--name',
        type=str,
        help='Server name (overrides config)'
    )
    
    parser.add_argument(
        '-p', '--port',
        type=int,
        help='Streaming port (overrides config, default: 12889)'
    )
    
    parser.add_argument(
        '--discovery-port',
        type=int,
        help='UDP discovery port (overrides config, default: 12888)'
    )

    parser.add_argument(
        '--web-port',
        type=int,
        help='Web Player port (overrides config, default: 8090)'
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    parser.add_argument(
        '--web-only',
        action='store_true',
        help='Disable local audio playback (serve only over HTTP/Web)'
    )

    parser.add_argument(
        '--list-configs',
        action='store_true',
        help='List available configurations'
    )
    
    args = parser.parse_args()
    
    # List configurations if requested
    if args.list_configs:
        print("\nAvailable AudioCast Server Configurations:\n")
        
        configs = {
            'basic': "Default configuration",
            'living_room': "Living room speaker (RaspberryPi)",
            'bedroom': "Bedroom speaker with low latency",
            'studio': "Studio monitor (96kHz, high quality)",
            'mono': "Budget speaker (mono, 16kHz)",
            'cloud': "Cloud streaming with high latency",
        }
        
        for name, desc in configs.items():
            print(f"  {name:15} - {desc}")
        
        print("\nUsage: python start.py -c <config_name>\n")
        return 0
    
    # Load configuration
    config = get_config(args.config)
    
    # Override with command-line arguments
    if args.name:
        config.SERVER_NAME = args.name
    
    if args.port:
        config.STREAMING_PORT = args.port
    
    if args.discovery_port:
        config.DISCOVERY_PORT = args.discovery_port

    if args.web_port:
        config.WEB_PORT = args.web_port

    if args.web_only:
        config.ENABLE_LOCAL_AUDIO = False

    # Print startup info
    print("\n" + "="*70)
    print("AudioCast Protocol Server - Starting")
    print("="*70)
    print(f"Configuration: {args.config}")
    print(f"Server Name:   {config.SERVER_NAME}")
    print(f"Stream Port:   {config.STREAMING_PORT} (TCP/WebSocket Input)")
    print(f"Web Port:      {config.WEB_PORT} (HTTP/WebSocket Output)")
    print(f"Discovery Port: {config.DISCOVERY_PORT} (UDP)")
    print(f"Audio:         {config.AUDIO.SAMPLE_RATE}Hz, {config.AUDIO.CHANNELS}ch")
    print("="*70 + "\n")
    
    if args.verbose:
        print("✓ Verbose logging enabled\n")
    
    # Run server
    try:
        exit_code = asyncio.run(run_server(config, args.verbose))
        return exit_code
    except KeyboardInterrupt:
        print("\n\nServer stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
