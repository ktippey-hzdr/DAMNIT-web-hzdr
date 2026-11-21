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

/**
 * The campaign-file dataset a data product can be previewed from, without
 * the leading slash, or undefined. Only products written into the campaign
 * file itself (no separate `path`) as a scalar, line or image; a LabFrog
 * shot-indexed table holds every shot, so a preview would not be this shot's.
 */
export function previewableProductDataset(
  product: HZDRDataProduct
): string | undefined {
  if (product.path || !product.dataset_name) return undefined
  if (product.metadata?.shot_indexed === true) return undefined
  const kind = PREVIEW_KINDS.includes(String(product.preview_kind))
    ? String(product.preview_kind)
    : kindFromShape(product.shape ?? [])
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
