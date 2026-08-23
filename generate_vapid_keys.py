"""
Generate a VAPID keypair for Web Push.

Run once, then set the printed values as environment variables. Keep the
private key secret; rotating it invalidates every existing subscription.

    python generate_vapid_keys.py
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main() -> None:
    key = ec.generate_private_key(ec.SECP256R1())

    public_bytes = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    private_value = key.private_numbers().private_value.to_bytes(32, "big")

    print("Add these to your environment (Render Environment tab):\n")
    print(f"VAPID_PUBLIC_KEY={b64(public_bytes)}")
    print(f"VAPID_PRIVATE_KEY={b64(private_value)}")
    print("VAPID_CONTACT_EMAIL=you@yourcollege.edu")
    print("\nKeep the private key secret. Changing it unsubscribes every device.")


if __name__ == "__main__":
    main()
