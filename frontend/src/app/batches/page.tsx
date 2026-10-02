import { redirect } from "next/navigation";

// Uploading and the list of past imports now live on /imports.
export default function Page() {
  redirect("/imports");
}
