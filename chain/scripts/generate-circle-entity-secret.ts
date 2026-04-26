import {
  registerEntitySecretCiphertext,
} from "@circle-fin/developer-controlled-wallets";
import { randomBytes } from "node:crypto";

async function main() {
  const apiKey = process.env.CIRCLE_API_KEY;
  if (!apiKey) {
    console.error("CIRCLE_API_KEY environment variable is required");
    process.exit(1);
  }

  console.log("\n=== Step 1: Generating Entity Secret ===");
  const entitySecret = randomBytes(32).toString("hex");
  console.log(`Entity Secret: ${entitySecret}`);
  console.log("\nStore this securely! You'll need it for all Circle API operations.");

  console.log("\n=== Step 2: Registering Entity Secret with Circle ===");
  console.log("This will encrypt the secret and register it with Circle...");

  try {
    const response = await registerEntitySecretCiphertext({
      apiKey,
      entitySecret,
    });

    console.log("\n=== Registration Successful ===");
    if (response.data?.recoveryFile) {
      console.log("Recovery file has been downloaded.");
      console.log("Recovery file content (base64):");
      console.log(response.data.recoveryFile.substring(0, 50) + "...");
    }

    console.log("\n=== Add to .env file ===");
    console.log(`CIRCLE_API_KEY=${apiKey}`);
    console.log(`CIRCLE_ENTITY_SECRET=${entitySecret}`);

    console.log("\n=== Next Steps ===");
    console.log("1. Store the entity secret securely");
    console.log("2. Save the recovery file that was downloaded (recovery_file_<timestamp>.dat)");
    console.log("3. Create a wallet set using the Circle client");
  } catch (error) {
    console.error("\nError registering entity secret:", error);
    if (error instanceof Error) {
      console.error("Message:", error.message);
    }
    process.exit(1);
  }
}

main();