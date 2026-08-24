import Link from 'next/link';

export default function HomePage() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col items-center justify-center px-6 py-24 text-center">
      <p className="mb-4 text-sm font-semibold uppercase tracking-[0.2em] text-fd-primary">
        Sugar Labs
      </p>
      <h1 className="max-w-4xl text-4xl font-bold tracking-tight sm:text-6xl">
        Sugar Activity Studio documentation
      </h1>
      <p className="mt-6 max-w-2xl text-lg text-fd-muted-foreground">
        Learn how the studio turns a plain-language idea into a validated,
        previewable, and installable Sugar activity—and how to contribute
        safely across the complete pipeline.
      </p>
      <div className="mt-10 flex flex-wrap justify-center gap-4">
        <Link
          href="/docs"
          className="rounded-full bg-fd-primary px-6 py-3 font-medium text-fd-primary-foreground"
        >
          Read the documentation
        </Link>
        <Link
          href="https://github.com/sugarlabs/Sugar-activity-on-Demand"
          className="rounded-full border px-6 py-3 font-medium"
        >
          View on GitHub
        </Link>
      </div>
    </main>
  );
}
