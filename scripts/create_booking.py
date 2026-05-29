#!/usr/bin/env python3
"""
Create manual bookings in Hospitable via MCP.
Hardened with proper error handling, validation, and retry logic.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class BookingRequest:
    """Represents a booking to be created."""
    property_id: str
    check_in: str
    check_out: str
    guest_first_name: str
    guest_last_name: str
    guest_email: str
    guest_phone: str = ""
    adults: int = 1
    children: int = 0
    infants: int = 0
    pets: int = 0
    rate_cents: int = 0
    currency: str = "USD"
    language: str = "en"
    channel: str = "direct"
    notes: str = ""
    
    def to_mcp_payload(self) -> Dict[str, Any]:
        """Convert to MCP create-reservation payload."""
        return {
            "property_id": self.property_id,
            "check_in": self.check_in,
            "check_out": self.check_out,
            "guests": {
                "adults": self.adults,
                "children": self.children,
                "infants": self.infants,
                "pets": self.pets
            },
            "guest": {
                "first_name": self.guest_first_name,
                "last_name": self.guest_last_name,
                "email": self.guest_email,
                "phone": self.guest_phone
            },
            "language": self.language,
            "financials": {
                "currency": self.currency,
                "accommodation": self.rate_cents
            },
            "channel": self.channel,
            "notes": self.notes or f"Manual booking - {self.guest_first_name} {self.guest_last_name}"
        }


class MCPClient:
    """Wrapper for mcporter MCP calls with proper JSON handling."""
    
    DEFAULT_TIMEOUT = 60
    DEFAULT_DELAY = 0.5
    MAX_RETRIES = 3
    
    def __init__(self, delay: float = DEFAULT_DELAY, config_path: Optional[str] = None):
        self.delay = delay
        self.config_path = config_path or os.environ.get(
            "MCPORTER_CONFIG", 
            "/home/umbrel/.openclaw/workspace/config/mcporter.json"
        )
    
    def _build_cmd(self, tool: str, **kwargs) -> List[str]:
        """Build mcporter command with config."""
        cmd = [
            "mcporter",
            "--config", self.config_path,
            "call", "hospitable", tool
        ]
        
        for key, value in kwargs.items():
            if isinstance(value, (dict, list)):
                cmd.append(f"{key}={json.dumps(value)}")
            elif isinstance(value, bool):
                cmd.append(f"{key}={'true' if value else 'false'}")
            elif value is None:
                continue
            else:
                cmd.append(f"{key}={value}")
        
        return cmd
    
    def _run(self, tool: str, **kwargs) -> Tuple[bool, Any]:
        """Execute mcporter call with proper argument formatting."""
        cmd = self._build_cmd(tool, **kwargs)
        
        for attempt in range(self.MAX_RETRIES):
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.DEFAULT_TIMEOUT
            )
            
            # Check stderr for validation errors first
            error_text = (result.stderr or "").strip()
            
            if "Property is not available" in error_text:
                return False, {"error": error_text, "type": "unavailable"}
            
            if "Unknown MCP server" in error_text:
                return False, {"error": error_text, "type": "auth"}
            
            if result.returncode != 0:
                return False, {"error": error_text or result.stdout.strip(), "type": "runtime"}
            
            # Try to parse JSON response
            output = result.stdout.strip()
            
            if output.startswith('{'):
                try:
                    data = json.loads(output)
                    # Check if it's an actual booking response
                    if "id" in data or "code" in data:
                        return True, data
                    else:
                        return False, {"error": "Invalid response structure", "data": data}
                except json.JSONDecodeError:
                    return False, {"error": f"Invalid JSON: {output[:200]}"}
            elif "field is required" in output.lower():
                # Validation error from MCP
                return False, {"error": output, "type": "validation"}
            elif output:
                # Non-JSON success case
                return True, {"raw": output}
            
            if attempt < self.MAX_RETRIES - 1:
                time.sleep(self.delay * (attempt + 1))
        
        return False, {"error": "Max retries exceeded"}
    
    def create_reservation(self, booking: BookingRequest) -> Tuple[bool, Any]:
        """Create a reservation via MCP."""
        payload = booking.to_mcp_payload()
        return self._run("create-reservation", **payload)
    
    def get_reservations(self, property_id: str, start_date: str, end_date: str) -> Tuple[bool, Any]:
        """List reservations for a property."""
        return self._run(
            "get-reservations",
            properties=[property_id],
            start_date=start_date,
            end_date=end_date
        )
    
    def get_property(self, property_id: str) -> Tuple[bool, Any]:
        """Get property details."""
        return self._run("get-property", uuid=property_id)


def create_single_booking(
    client: MCPClient,
    booking: BookingRequest,
    dry_run: bool = False
) -> Tuple[bool, Any]:
    """Create a single booking with error handling."""
    
    if dry_run:
        return True, {
            "dry_run": True,
            "payload": booking.to_mcp_payload()
        }
    
    return client.create_reservation(booking)


def main():
    ap = argparse.ArgumentParser(
        description="Create manual bookings in Hospitable via MCP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single booking
  python3 create_booking.py \\
    --property-id afe45be5-4776-446e-b397-1968ce334961 \\
    --check-in 2026-06-01 --check-out 2026-06-05 \\
    --first-name Joseph --last-name Cone \\
    --email jenny@tenmconstruction.com \\
    --phone "(680) 290-4541" \\
    --rate 68750

  # From JSON file
  python3 create_booking.py --json-file booking.json

Environment:
  MCPORTER_CONFIG    Path to mcporter config (default: ~/.openclaw/workspace/config/mcporter.json)
        """
    )
    
    # Property selection
    ap.add_argument("--property-id", help="Hospitable property UUID")
    
    # Dates
    ap.add_argument("--check-in", help="Check-in date YYYY-MM-DD")
    ap.add_argument("--check-out", help="Check-out date YYYY-MM-DD")
    
    # Guest info
    ap.add_argument("--first-name", help="Guest first name")
    ap.add_argument("--last-name", help="Guest last name")
    ap.add_argument("--email", help="Guest email")
    ap.add_argument("--phone", default="", help="Guest phone")
    ap.add_argument("--adults", type=int, default=1, help="Number of adults")
    ap.add_argument("--children", type=int, default=0, help="Number of children")
    ap.add_argument("--infants", type=int, default=0, help="Number of infants")
    ap.add_argument("--pets", type=int, default=0, help="Number of pets")
    
    # Financial
    ap.add_argument("--rate", type=int, default=0, help="Rate in cents (e.g., 68750 for $687.50)")
    ap.add_argument("--currency", default="USD", help="Currency code")
    
    # Other
    ap.add_argument("--language", default="en", help="Guest language")
    ap.add_argument("--channel", default="direct", help="Booking channel")
    ap.add_argument("--notes", default="", help="Booking notes")
    
    # Input file
    ap.add_argument("--json-file", help="JSON file with booking data")
    
    # Control
    ap.add_argument("--dry-run", action="store_true", help="Preview without creating")
    ap.add_argument("--delay", type=float, default=0.5, help="Delay between requests")
    ap.add_argument("--quiet", "-q", action="store_true", help="Minimal output")
    
    args = ap.parse_args()
    
    # Load from JSON file if provided
    if args.json_file:
        with open(args.json_file) as f:
            data = json.load(f)
        booking = BookingRequest(**data)
    else:
        # Validate required args
        required = ["property_id", "check_in", "check_out", "first_name", "last_name", "email"]
        missing = [f"--{r.replace('_', '-')}" for r in required if getattr(args, r) is None]
        if missing:
            print(f"Error: Missing required arguments: {', '.join(missing)}", file=sys.stderr)
            return 1
        
        booking = BookingRequest(
            property_id=args.property_id,
            check_in=args.check_in,
            check_out=args.check_out,
            guest_first_name=args.first_name,
            guest_last_name=args.last_name,
            guest_email=args.email,
            guest_phone=args.phone,
            adults=args.adults,
            children=args.children,
            infants=args.infants,
            pets=args.pets,
            rate_cents=args.rate,
            currency=args.currency,
            language=args.language,
            channel=args.channel,
            notes=args.notes
        )
    
    client = MCPClient(delay=args.delay)
    
    if not args.quiet:
        print(f"Creating booking for {booking.guest_first_name} {booking.guest_last_name}")
        print(f"  Property: {booking.property_id}")
        print(f"  Dates: {booking.check_in} → {booking.check_out}")
        if booking.rate_cents:
            print(f"  Rate: ${booking.rate_cents/100:.2f}")
    
    success, result = create_single_booking(client, booking, dry_run=args.dry_run)
    
    if success:
        if args.dry_run:
            if not args.quiet:
                print("\n[DRY RUN] Payload preview:")
                print(json.dumps(result["payload"], indent=2))
        else:
            booking_id = result.get("id", "N/A")
            code = result.get("code", "N/A")
            if not args.quiet:
                print(f"\n✓ Created: {booking_id} (Code: {code})")
            else:
                print(json.dumps({"success": True, "id": booking_id, "code": code}))
        return 0
    else:
        error = result.get("error", "Unknown error")
        error_type = result.get("type", "unknown")
        
        if not args.quiet:
            if error_type == "unavailable":
                print(f"\n✗ Failed: Property not available on those dates")
            else:
                print(f"\n✗ Failed ({error_type}): {error}")
        else:
            print(json.dumps({"success": False, "error": error, "type": error_type}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
