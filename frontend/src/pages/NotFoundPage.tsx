import { PageHeader } from "../layout/PageHeader";
import { NotFoundState } from "../ui/states";

/** Unknown URL inside the app. Says nothing about whether any record exists. */
export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" />
      <NotFoundState message="Page not found." />
    </>
  );
}
