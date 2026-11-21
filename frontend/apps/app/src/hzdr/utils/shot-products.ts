import type { HZDRDataProduct } from '../types'

const PREVIEW_KINDS = ['scalar', 'line', 'image']

function kindFromShape(shape: Array<number | string>) {
  if (!shape.length || (shape.length === 1 && Number(shape[0]) === 1)) {
    return 'scalar'
  }
  if (shape.length === 1) return 'line'
  if (shape.length === 2) return 'image'
  return 'stack'
}

function fileName(path: string) {
  return path.replace(/\\/g, '/').split('/').filter(Boolean).at(-1) ?? ''
}

/**
 * Whether a product lives in the campaign file: no separate `path`, or the
 * campaign file's own (the builder records an in-file product's path as the
 * file it wrote, maybe under another host's mount, hence the name check).
 */
function isInCampaignFile(
  path: string | null | undefined,
  campaignFile?: string | null
) {
  if (!path) return true
  if (!campaignFile) return false
  return path === campaignFile || fileName(path) === fileName(campaignFile)
}

/**
 * The campaign-file dataset a data product can be previewed from, without
 * the leading slash, or undefined. Only products written into the campaign
 * file itself (`campaignFile`, the shot's `hdf5_path`) as a scalar, line or
 * image; a LabFrog shot-indexed table holds every shot, so a preview would
 * not be this shot's.
 */
export function previewableProductDataset(
  product: HZDRDataProduct,
  campaignFile?: string | null
): string | undefined {
  if (!product.dataset_name || !isInCampaignFile(product.path, campaignFile))
    return undefined
  if (product.metadata?.shot_indexed === true) return undefined
  const kind = product.preview_kind ?? kindFromShape(product.shape ?? [])
  if (!PREVIEW_KINDS.includes(kind)) return undefined
  return product.dataset_name.replace(/^\/+/, '') || undefined
}

/**
 * Context Builder, opened with this HDF5 dataset as the column's input:
 * `?add=hdf5:<dataset name>`, with `:` and `/` left readable.
 */
export function shotAddColumnPath(sourceKey: string, datasetName: string) {
  const value = encodeURIComponent(`hdf5:${datasetName}`).replace(
    /%3A|%2F/gi,
    (code) => decodeURIComponent(code)
  )
  return `/source/${sourceKey}/context-builder?add=${value}`
}
