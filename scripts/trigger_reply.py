import sys
import subprocess
import json

def get_reservation_details(v2_key, reservation_id):
    """Fetch reservation context from V2 API."""
    url = f"https://public.api.hospitable.com/v2/reservations/{reservation_id}"
    cmd = [
        "curl", "-s", "-X", "GET", url,
        "-H", f"Authorization: Bearer {v2_key}",
        "-H", "Accept: application/json"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return json.loads(result.stdout)
    except:
        return None

def trigger_ai_reply(v2_key, reservation_id, dry_run=True):
    """
    Automates the Hospitable AI Suggested Reply workflow.
    Mode: OpenClaw Suggest (Uses Agent AI for drafting + V2 Public API for sending).
    """
    public_url = "https://public.api.hospitable.com/v2"
    
    print(f"🔄 Fetching context for Reservation: {reservation_id}...")
    res_data = get_reservation_details(v2_key, reservation_id)
    
    guest_name = "Guest"
    property_name = "the property"
    
    if res_data and "data" in res_data:
        # Note: res_data['data'] is often a list or a single object depending on the V2 endpoint behavior
        data = res_data["data"]
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
        
        # We'd normally pull more info here, but for the preview we'll use available metadata
        guest_name = data.get("guests", {}).get("first_name", "Guest")
        # Property info is usually a separate fetch, but we can infer or use a generic term
    
    # This is the "OpenClaw Suggest" part. The agent generates this in the main turn.
    # For the script execution, we'll provide a high-quality template that the agent can override.
    suggestion = f"Hi {guest_name}, thank you for reaching out! We're looking forward to hosting you at {property_name}. Please let us know if you have any other questions before your arrival!"

    print("\n--- 📝 AI SUGGESTION PREVIEW ---")
    print(f"To: {guest_name}")
    print(f"Content: {suggestion}")
    print("-------------------------------\n")

    if dry_run:
        print("⚠️ DRY RUN: Message not sent.")
        return suggestion

    print("🚀 Submitting response to guest via V2 API...")
    msg_cmd = [
        "curl", "-s", "-X", "POST",
        f"{public_url}/reservations/{reservation_id}/messages",
        "-H", f"Authorization: Bearer {v2_key}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"body": suggestion})
    ]
    
    try:
        send_result = subprocess.run(msg_cmd, capture_output=True, text=True)
        send_data = json.loads(send_result.stdout)
        
        if "data" in send_data and "sent_reference_id" in send_data["data"]:
            print(f"✅ Successfully sent reply to reservation {reservation_id}")
            print(f"🆔 Reference: {send_data['data']['sent_reference_id']}")
        else:
            print(f"❌ Error sending message: {send_result.stdout}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: trigger_reply.py <v2_key> <reservation_id> [--send]")
    else:
        is_dry_run = "--send" not in sys.argv
        trigger_ai_reply(sys.argv[1], sys.argv[2], dry_run=is_dry_run)
