import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium leading-4 whitespace-nowrap border [&_svg]:size-3",
  {
    variants: {
      tone: {
        neutral: "border-border-strong bg-panel-2 text-muted",
        good: "border-good/30 bg-good/10 text-good",
        warning: "border-warning/30 bg-warning/10 text-warning",
        serious: "border-serious/30 bg-serious/10 text-serious",
        critical: "border-critical/35 bg-critical/10 text-critical",
        info: "border-info/30 bg-info/10 text-info",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>["tone"]>;

export function Badge({
  className,
  tone,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
