import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export default function CheckoutCancelPage() {
  return (
    <Card className="mx-auto mt-10 max-w-lg p-6 text-center">
      <h1 className="text-lg font-semibold">PayPal Sandbox checkout cancelled</h1>
      <p className="mt-1 text-sm text-muted">The sandbox buyer did not approve the order. Nothing was captured.</p>
      <Button asChild variant="outline" size="sm" className="mt-5">
        <Link href="/">Back to PayFlow</Link>
      </Button>
    </Card>
  );
}
