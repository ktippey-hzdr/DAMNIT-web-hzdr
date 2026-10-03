import { Navigate, useLocation } from 'react-router'

/**
 * `/link-shot-records` was the Review matches page's path while it was "Link
 * Existing Shot Records". Keep its query and land on `/review-matches`, so
 * existing links and bookmarks do not break.
 */
export function LegacyReviewMatchesRedirect() {
  const { search, hash } = useLocation()
  return <Navigate to={{ pathname: '/review-matches', search, hash }} replace />
}
