// Package main contains an example.
package main

import (
	"log"

	"github.com/bluenviron/gortsplib/v4"
	"github.com/bluenviron/gortsplib/v4/pkg/base"
	"github.com/bluenviron/gortsplib/v4/pkg/description"
	"github.com/bluenviron/gortsplib/v4/pkg/format"
	"github.com/pion/rtp"
)

// This example shows how to
// 1. connect to a RTSP server.
// 2. read all media streams on a path.
// 3. Get PTS and NTP of incoming RTP packets.
// 4. Print frame size and timestamp.

func main() {
	// parse URL
	u, err := base.ParseURL("rtsp://mmtx01.apagaofogo.eco.br:8554/AoF-C0029-APASC")
	if err != nil {
		panic(err)
	}

	c := gortsplib.Client{
		Scheme: u.Scheme,
		Host:   u.Host,
	}

	// connect to the server
	err = c.Start2()
	if err != nil {
		panic(err)
	}
	defer c.Close()

	// find available medias
	desc, _, err := c.Describe(u)
	if err != nil {
		panic(err)
	}

	// setup all medias
	err = c.SetupAll(desc.BaseURL, desc.Medias)
	if err != nil {
		panic(err)
	}

	// called when a RTP packet arrives
	c.OnPacketRTPAny(func(medi *description.Media, _ format.Format, pkt *rtp.Packet) {
		// get the PTS (presentation timestamp) of the packet
		pts, ptsAvailable := c.PacketPTS2(medi, pkt)
		log.Printf("PTS: available=%v, value=%v\n", ptsAvailable, pts)

		// get the NTP (absolute timestamp) of the packet
		ntp, ntpAvailable := c.PacketNTP(medi, pkt)
		log.Printf("NTP: available=%v, value=%v\n", ntpAvailable, ntp)

		// reserve the frame (payload)
		frame := pkt.Payload

		// print frame size and timestamp if PTS is available
		if ptsAvailable {
			log.Printf("Frame: tamanho=%d, timestamp=%.3f\n", len(frame), float64(pts)/90000.0)
		}
	})

	// start playing
	_, err = c.Play(nil)
	if err != nil {
		panic(err)
	}

	// wait until a fatal error
	panic(c.Wait())
}
