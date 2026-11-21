import { type ReactNode, useEffect, useState } from 'react'
import { Anchor, Group, Text } from '@mantine/core'
import { Link } from 'react-router'

import { requireJson } from '../utils/api'

type ScicatInfo = {
  configured: boolean
  registered: boolean
  pid: string | null
  dataset_url: string | null
}

type WikiInfo = {
  configured: boolean
  page_title: string | null
  page_url: string | null
}

type ProducerStatus = {
  watchdog_hosts: { host: string }[]
  shotcounter: { status: string; last_event_at: string | null }
}

function Item({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Text size="sm" span>
      <Text span c="dimmed" inherit>
        {label}:{' '}
      </Text>
      {children}
    </Text>
  )
}

/**
 * One line above the shot table: the campaign's SciCat dataset, its wiki page
 * and the producers the catalog has seen. Each comes from its own optional
 * endpoint and fails on its own; the shot table stays usable either way.
 */
export function CampaignCards({ sourceKey }: { sourceKey: string }) {
  const [scicat, setScicat] = useState<ScicatInfo>()
  const [wiki, setWiki] = useState<WikiInfo>()
  const [producers, setProducers] = useState<ProducerStatus>()

  useEffect(() => {
    let current = true
    setScicat(undefined)
    setWiki(undefined)
    setProducers(undefined)
    const base = `/metadata/hzdr/sources/${encodeURIComponent(sourceKey)}`
    fetch(`${base}/scicat`)
      .then((response) => requireJson<ScicatInfo>(response))
      .then((value) => current && setScicat(value))
      .catch(() => undefined)
    fetch(`${base}/wiki`)
      .then((response) => requireJson<WikiInfo>(response))
      .then((value) => current && setWiki(value))
      .catch(() => undefined)
    fetch(`${base}/producer-status`)
      .then((response) => requireJson<ProducerStatus>(response))
      .then((value) => current && setProducers(value))
      .catch(() => undefined)
    return () => {
      current = false
    }
  }, [sourceKey])

  const watchdogs = producers?.watchdog_hosts.length

  return (
    <Group gap="lg" wrap="wrap" role="group" aria-label="Campaign status">
      <Item label="SciCat">
        {scicat?.dataset_url ? (
          <Anchor href={scicat.dataset_url} target="_blank" rel="noreferrer">
            {scicat.pid ?? 'Open dataset'}
          </Anchor>
        ) : scicat?.registered ? (
          `${scicat.pid} (no link configured)`
        ) : (
          'not registered yet'
        )}
      </Item>
      <Item label="Wiki">
        {wiki?.page_url ? (
          <Anchor href={wiki.page_url} target="_blank" rel="noreferrer">
            {wiki.page_title ?? 'Campaign wiki'}
          </Anchor>
        ) : (
          'no page linked'
        )}
      </Item>
      <Item label="Producers">
        <Anchor component={Link} to="/flow-monitor">
          {producers
            ? `Shotcounter ${producers.shotcounter.status}, ${watchdogs} ${
                watchdogs === 1 ? 'watchdog' : 'watchdogs'
              }`
            : 'Flow monitor'}
        </Anchor>
      </Item>
    </Group>
  )
}
